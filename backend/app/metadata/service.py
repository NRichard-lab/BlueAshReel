"""Local enrichment lifecycle. Browser catalog reads never instantiate a provider."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.metadata.artwork import cache_image
from app.metadata.matching import choose_match
from app.metadata.provider import MetadataProvider, ProviderError
from app.metadata.tmdb import TMDBMetadataProvider
from app.metadata.types import Candidate, MetadataDetails
from app.models import (
    BackgroundJob,
    Episode,
    Library,
    LocalArtwork,
    MediaFile,
    MediaItem,
    MetadataRecord,
    Season,
    Series,
    utcnow,
)
from app.services.filename_parser import parse_filename


def configured_provider(config: AppConfig) -> MetadataProvider | None:
    token = config.tmdb_token
    return (
        TMDBMetadataProvider(
            token,
            timeout_seconds=config.tmdb_timeout_seconds,
            retries=config.tmdb_retries,
            language=config.metadata_language,
            region=config.metadata_region,
        )
        if token
        else None
    )


def record_for(db: Session, item: MediaItem | Season) -> MetadataRecord:
    season = isinstance(item, Season)
    row = db.scalar(
        select(MetadataRecord).where(
            MetadataRecord.season_id == item.id if season else MetadataRecord.media_item_id == item.id
        )
    )
    if row is None:
        row = MetadataRecord(
            season_id=item.id if season else None,
            media_item_id=None if season else item.id,
            kind="season" if isinstance(item, Season) else item.kind,
        )
        db.add(row)
        db.flush()
    return row


def local_query(db: Session, item: MediaItem) -> tuple[str, int | None, float | None]:
    # Only the parsed title/year leave this Agent; never the filename or watch history.
    source = db.scalar(select(MediaFile).where(MediaFile.media_item_id == item.id).order_by(MediaFile.id).limit(1))
    if source and item.kind == "movie":
        parsed = parse_filename(source.relative_path, "movies")
        return parsed.title, parsed.year or item.year, source.duration_seconds
    return item.title, item.year, source.duration_seconds if source else None


class Enricher:
    def __init__(self, db: Session, config: AppConfig, provider: MetadataProvider | None):
        self.db, self.config, self.provider = db, config, provider
        self.visited: set[str] = set()
        self.seasons: set[str] = set()
        self.stop_provider = False
        self.retry_delay_seconds = 15 * 60.0

    def _due(self, row: MetadataRecord, force: bool) -> bool:
        if force:
            return True
        if not row.auto_match_enabled:
            return False
        if row.status == "complete" and row.metadata_updated_at:
            return row.metadata_updated_at.replace(tzinfo=UTC) < utcnow() - timedelta(
                days=self.config.metadata_refresh_days
            )
        if row.attempted_at and row.status in {"error", "needs_review", "unmatched"}:
            return row.attempted_at.replace(tzinfo=UTC) < utcnow() - timedelta(hours=6)
        return True

    def enrich(self, item: MediaItem, *, force: bool = False) -> MetadataRecord:
        row = record_for(self.db, item)
        if item.id in self.visited or not self._due(row, force):
            return row
        self.visited.add(item.id)
        if self.provider is None:
            if not row.title:
                row.status, row.error_code = "unavailable", "provider_not_configured"
            self.db.commit()
            return row
        if self.stop_provider:
            return row
        row.attempted_at, row.error_code = utcnow(), None
        try:
            if item.kind == "episode":
                self._episode(item, row, force)
            elif item.kind in {"movie", "series"}:
                if row.provider_id and row.provider != self.provider.name:
                    raise ProviderError("provider_unavailable")
                if not row.provider_id:
                    title, year, runtime = local_query(self.db, item)
                    candidates = (
                        self.provider.search_movie(title, year)
                        if item.kind == "movie"
                        else (self.provider.search_series(title, year))
                    )
                    decision = choose_match(title, year, candidates, kind=item.kind, runtime_seconds=runtime)
                    row.status, row.match_confidence, row.match_method = (
                        decision.status,
                        decision.confidence,
                        decision.method,
                    )
                    if decision.status != "matched" or decision.candidate is None:
                        self.db.commit()
                        return row
                    row.provider, row.provider_id = decision.candidate.provider, decision.candidate.provider_id
                    row.matched_at = utcnow()
                row.status = "fetching"
                self.db.commit()
                details = (
                    self.provider.get_movie(row.provider_id)
                    if item.kind == "movie"
                    else (self.provider.get_series(row.provider_id))
                )
                # Search results generally omit runtime. Recheck against fetched detail before
                # accepting an automatic movie match, using the exact same deterministic score.
                if item.kind == "movie" and not row.manually_confirmed and row.metadata_updated_at is None:
                    title, year, runtime = local_query(self.db, item)
                    candidate = Candidate(
                        details.provider,
                        details.provider_id,
                        details.kind,
                        details.title,
                        details.original_title,
                        details.year,
                        details.runtime_seconds,
                    )
                    check = choose_match(title, year, [candidate], kind="movie", runtime_seconds=runtime)
                    if check.status != "matched":
                        row.status, row.match_confidence, row.match_method = (
                            check.status,
                            check.confidence,
                            check.method,
                        )
                        row.provider_id = None
                        self.db.commit()
                        return row
                    row.match_confidence, row.match_method = check.confidence, check.method
                self._persist(item, row, details)
            else:
                row.status = "unmatched"
        except ProviderError as exc:
            row.status, row.error_code = "error", exc.code[:80]
            self.retry_delay_seconds = max(self.retry_delay_seconds, exc.retry_after_seconds or 0)
            if (
                exc.status_code in {401, 403, 429}
                or (exc.status_code or 0) >= 500
                or exc.code in {"timeout", "network_error", "temporarily_unavailable"}
            ):
                self.stop_provider = True
        except (OSError, ValueError):
            row.status, row.error_code = "error", "local_metadata_error"
        self.db.commit()
        return row

    def _episode(self, item: MediaItem, row: MetadataRecord, force: bool) -> None:
        assert self.provider is not None
        episode = self.db.get(Episode, item.id)
        season = self.db.get(Season, episode.season_id) if episode else None
        series = self.db.get(Series, season.series_id) if season else None
        show = self.db.get(MediaItem, series.media_item_id) if series else None
        if not episode or not season or not show:
            raise ProviderError("hierarchy_unavailable")
        show_metadata = self.enrich(show, force=force)
        if (
            not show_metadata.provider_id
            or self.stop_provider
            or show_metadata.provider != self.provider.name
            or show_metadata.status not in {"complete", "matched"}
        ):
            row.status = show_metadata.status if show_metadata.status != "complete" else "unmatched"
            row.error_code = show_metadata.error_code
            return
        season_row = record_for(self.db, season)
        if season.id not in self.seasons:
            self.seasons.add(season.id)
            if self._due(season_row, force):
                season_row.attempted_at, season_row.status = utcnow(), "fetching"
                self.db.commit()
                try:
                    details = self.provider.get_season(show_metadata.provider_id, season.season_number)
                    season_row.match_method, season_row.match_confidence = (
                        "series_hierarchy",
                        show_metadata.match_confidence,
                    )
                    self._persist(show, season_row, details, season_number=season.season_number)
                except ProviderError as exc:
                    season_row.status, season_row.error_code = "error", exc.code
                    self.db.commit()
                    raise
                self.db.commit()
        row.status = "fetching"
        row.match_method, row.match_confidence = "series_hierarchy", show_metadata.match_confidence
        self.db.commit()
        details = self.provider.get_episode(show_metadata.provider_id, season.season_number, episode.episode_number)
        self._persist(item, row, details)

    def _persist(
        self,
        item: MediaItem,
        row: MetadataRecord,
        details: MetadataDetails,
        *,
        season_number: int | None = None,
    ) -> None:
        assert self.provider is not None
        scalar_fields = (
            "provider",
            "provider_id",
            "title",
            "original_title",
            "year",
            "release_date",
            "last_air_date",
            "runtime_seconds",
            "overview",
            "tagline",
            "original_language",
            "content_rating",
            "vote_average",
            "vote_count",
            "number_of_seasons",
            "number_of_episodes",
        )
        for field in scalar_fields:
            setattr(row, field, getattr(details, field))
        for field in ("genres", "studios", "networks", "creators", "countries", "related_provider_ids"):
            setattr(row, field, list(getattr(details, field)))
        row.external_ids = dict(details.external_ids)
        row.series_status = details.status
        row.provider_data_updated_at = utcnow()  # Local fetch time; not a claimed TMDB modification date.
        row.matched_at = row.matched_at or utcnow()
        row.error_code = None
        images = list(details.artwork)
        images.extend(credit.profile for credit in details.credits[:10] if credit.profile is not None)
        profile_ids: dict[str, str] = {}
        retained: set[str] = set()
        image_error: ProviderError | None = None
        for source in dict.fromkeys(images):
            if season_number is not None and source.kind != "poster":
                continue
            try:
                art = cache_image(
                    self.db,
                    self.config,
                    self.provider,
                    item,
                    source,
                    artwork_type=f"season_poster_{season_number}" if season_number is not None else None,
                )
                retained.add(art.id)
                if source.person_provider_id:
                    profile_ids[source.person_provider_id] = art.id
            except ProviderError as exc:
                image_error = exc
                break
        row.credits = [
            {
                "name": credit.name,
                "character": credit.character,
                "job": credit.job,
                "department": credit.role,
                "person_provider_id": credit.provider_person_id,
                "profile_url": f"/api/v1/browse/artwork/{profile_ids[credit.provider_person_id]}"
                if credit.provider_person_id in profile_ids
                else None,
            }
            for credit in details.credits[:40]
        ]
        if image_error:
            raise image_error
        types = [f"season_poster_{season_number}"] if season_number is not None else ["poster", "background", "profile"]
        for stale in self.db.scalars(
            select(LocalArtwork).where(
                LocalArtwork.media_item_id == item.id,
                LocalArtwork.provider == details.provider,
                LocalArtwork.artwork_type.in_(types),
                LocalArtwork.id.not_in(retained),
            )
        ):
            self.db.delete(stale)
        row.status, row.metadata_updated_at = "complete", utcnow()


def search_candidates(db: Session, provider: MetadataProvider, media_id: str) -> tuple[Candidate, ...]:
    item = db.get(MediaItem, media_id)
    if item is None or item.kind not in {"movie", "series"}:
        raise ValueError("Search requires a local movie or series")
    title, year, _ = local_query(db, item)
    return provider.search_movie(title, year) if item.kind == "movie" else provider.search_series(title, year)


def assign_match(db: Session, media_id: str, provider: str, provider_id: str) -> MetadataRecord:
    item = db.get(MediaItem, media_id)
    if item is None or item.kind not in {"movie", "series"} or not provider or not provider_id:
        raise ValueError("Assign requires a local movie or series and provider identity")
    clear_match(db, media_id)
    row = record_for(db, item)
    row.provider, row.provider_id = provider[:32], provider_id[:100]
    row.manually_confirmed, row.auto_match_enabled = True, True
    row.status, row.match_method, row.match_confidence = "matched", "manual", 1.0
    row.matched_at = utcnow()
    db.commit()
    return row


def clear_match(db: Session, media_id: str) -> None:
    item = db.get(MediaItem, media_id)
    if item is None:
        raise ValueError("Local media unavailable")
    affected = [item.id]
    season_ids: list[str] = []
    if item.kind == "series":
        season_ids = list(db.scalars(select(Season.id).join(Series).where(Series.media_item_id == item.id)))
        affected += list(db.scalars(select(Episode.media_item_id).where(Episode.season_id.in_(season_ids))))
    for target in db.scalars(
        select(MetadataRecord).where(
            MetadataRecord.media_item_id.in_(affected) | MetadataRecord.season_id.in_(season_ids)
        )
    ):
        db.delete(target)
    for art in db.scalars(
        select(LocalArtwork).where(LocalArtwork.media_item_id.in_(affected), LocalArtwork.provider.is_not(None))
    ):
        db.delete(art)
    db.flush()
    row = record_for(db, item)
    row.auto_match_enabled = False
    db.commit()


def enqueue_enrichment(
    db: Session,
    library_id: str,
    *,
    force: bool = False,
    retry_only: bool = False,
) -> BackgroundJob:
    existing = db.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.job_type == "metadata_enrich",
            BackgroundJob.status.in_(["queued", "running", "retry_wait"]),
            BackgroundJob.payload["library_id"].as_string() == library_id,
        )
        .limit(1)
    )
    if existing:
        return existing
    job = BackgroundJob(
        job_type="metadata_enrich",
        priority=150,
        payload={"library_id": library_id, "force": force, "retry_only": retry_only},
    )
    db.add(job)
    db.flush()
    return job


def run_metadata_job(
    db: Session,
    job: BackgroundJob,
    config: AppConfig,
    stop_requested: Callable[[], bool] | None = None,
) -> None:
    from app.services.scanner import ScanInterrupted

    provider = configured_provider(config)
    enricher = Enricher(db, config, provider)
    try:
        library = db.get(Library, job.payload.get("library_id"))
        if library and library.enabled:
            cursor = ""
            while True:
                items = list(
                    db.scalars(
                        select(MediaItem)
                        .where(
                            MediaItem.library_id == library.id,
                            MediaItem.available.is_(True),
                            MediaItem.kind.in_(["movie", "series", "episode"]),
                            MediaItem.id > cursor,
                        )
                        .order_by(MediaItem.id)
                        .limit(50)
                    )
                )
                if not items:
                    break
                for item in items:
                    if stop_requested and stop_requested():
                        raise ScanInterrupted("Metadata enrichment retained for restart")
                    db.refresh(job, attribute_names=["cancel_requested"])
                    if job.cancel_requested:
                        job.status, job.completed_at = "cancelled", utcnow()
                        db.commit()
                        return
                    job.heartbeat_at = utcnow()
                    job.lease_expires_at = utcnow() + timedelta(minutes=15)
                    db.commit()
                    current = record_for(db, item)
                    cursor = item.id
                    if job.payload.get("retry_only") and current.status not in {
                        "error",
                        "unavailable",
                        "matched",
                        "fetching",
                    }:
                        continue
                    retry_error = job.attempts > 1 and current.status == "error"
                    requested_refresh = bool(job.payload.get("force")) and job.attempts <= 1
                    enricher.enrich(item, force=requested_refresh or retry_error or bool(job.payload.get("retry_only")))
                    job.progress_current += 1
                    db.commit()
                if enricher.stop_provider:
                    break
        if enricher.stop_provider:
            from app.services.jobs import fail_job

            fail_job(db, job, "Metadata provider unavailable; indexed media remains playable")
            if job.status == "retry_wait":
                job.available_at = utcnow() + timedelta(seconds=enricher.retry_delay_seconds)
                db.commit()
            return
        job.status = "succeeded"
        job.completed_at = utcnow()
        job.locked_by, job.lease_expires_at = None, None
        db.commit()
    finally:
        if provider:
            provider.close()
