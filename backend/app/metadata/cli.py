"""Private Agent maintenance commands; no metadata or secrets are stored on the Portal."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Blue Ash Reel local metadata maintenance")
    parser.add_argument("--data-dir", type=Path, help="Installed native Agent data directory")
    parser.add_argument("operation", choices=["status", "enrich", "refresh", "retry", "search", "assign", "clear"])
    parser.add_argument("--media-id")
    parser.add_argument("--library-id")
    parser.add_argument("--provider", default="tmdb")
    parser.add_argument("--provider-id")
    args = parser.parse_args()
    if args.data_dir:
        from app.native_runtime import activate_configuration, load_configuration, load_installation

        installation = load_installation(args.data_dir)
        activate_configuration(installation, load_configuration(installation))
    from sqlalchemy import func, select

    from app.config import get_config
    from app.database import SessionLocal
    from app.metadata.service import (
        Enricher,
        assign_match,
        clear_match,
        configured_provider,
        enqueue_enrichment,
        search_candidates,
    )
    from app.models import Library, MediaItem, MetadataRecord
    from app.services.outbound import install_native_network_guard

    config = get_config()
    install_native_network_guard(config, allow_portal=True)
    with SessionLocal() as db:
        if args.operation == "status":
            print(
                json.dumps(
                    {
                        "provider": "tmdb",
                        "configured": bool(config.tmdb_token),
                        "states": {
                            state: count
                            for state, count in db.execute(
                                select(MetadataRecord.status, func.count()).group_by(MetadataRecord.status)
                            )
                        },
                    }
                )
            )
            return 0
        if args.operation in {"search", "assign", "clear"} and not args.media_id:
            parser.error("This operation requires --media-id (local Agent ID)")
        if args.operation == "clear":
            clear_match(db, args.media_id)
            print(json.dumps({"status": "unmatched", "automatic_matching": False}))
            return 0
        if args.operation == "assign":
            if not args.provider_id:
                parser.error("Assign requires --provider-id")
            assign_match(db, args.media_id, args.provider, args.provider_id)
        provider = configured_provider(config)
        try:
            if args.operation == "search":
                if provider is None:
                    print(json.dumps({"status": "unavailable", "error_code": "provider_not_configured"}))
                    return 2
                print(
                    json.dumps(
                        {"candidates": [asdict(value) for value in search_candidates(db, provider, args.media_id)]}
                    )
                )
            elif args.media_id:
                item = db.get(MediaItem, args.media_id)
                if item is None:
                    parser.error("Local media unavailable")
                row = Enricher(db, config, provider).enrich(item, force=args.operation != "enrich")
                print(json.dumps({"status": row.status, "error_code": row.error_code}))
            else:
                libraries = select(Library.id).where(Library.enabled.is_(True))
                if args.library_id:
                    libraries = libraries.where(Library.id == args.library_id)
                jobs = [
                    enqueue_enrichment(
                        db,
                        library_id,
                        force=args.operation in {"refresh", "retry"},
                        retry_only=args.operation == "retry",
                    ).id
                    for library_id in db.scalars(libraries)
                ]
                db.commit()
                print(json.dumps({"queued_jobs": jobs, "configured": bool(provider)}))
        finally:
            if provider:
                provider.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
