'use client';
import { Library, ArrowRight } from "lucide-react";
import Link from "next/link";
import { CatalogState } from "@/components/viewer-catalog";
import { MediaRow } from "@/components/viewer/media-row";
import type { HomeResponse, HomeLibrary } from "@/lib/viewer";
import { useViewerData } from "@/lib/viewer";


/** Rails come from the existing local `GET /browse/home` endpoint, including viewer-authorized libraries. No demo fallback. */
const realRails: [key: "continue" | "recent_movies" | "recent_episodes", title: string, href: string, empty: string][] = [
  [
    "continue",
    "Continue Watching",
    "/continue",
    "No titles in progress.",
  ],
  [
    "recent_movies",
    "Recently Added Movies",
    "/movies",
    "No movies found.",
  ],
  [
    "recent_episodes",
    "Recently Added TV",
    "/shows",
    "No TV shows found.",
  ],
];

export function ViewerHome() {
  const home = useViewerData<HomeResponse>("/browse/home");
  const error = home.error || (home.data && !Array.isArray(home.data.libraries)
    ? "Update your Agent to load the complete Home page." : "");
  const showRails = !home.loading && !error && !!home.data;

  return (
    <>
      <div className="mb-9 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-primary">
            Your private collection
          </p>
          <h1 className="text-3xl font-semibold tracking-tight md:text-4xl">
            Something worth staying in for.
          </h1>
          <p className="mt-3 text-sm text-muted-foreground">
            Movies and television, streamed from your own workstation.
          </p>
        </div>
        <Link
          href="/search"
          className="inline-flex items-center gap-2 rounded-lg border border-border px-4 py-2 text-sm outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
        >
          Find something to watch{" "}
          <ArrowRight className="size-4" aria-hidden="true" />
        </Link>
      </div>

      <CatalogState {...home} error={error} empty={false} />

      {showRails && (
        <>
          {realRails.map(([key, title, href, empty]) => (
            <MediaRow
              key={key}
              title={title}
              href={href}
              items={home.data?.[key] ?? []}
              onChanged={home.reload}
              emptyState={empty}
            />
          ))}

          <MyLibrariesRow libraries={home.data?.libraries ?? []} />
        </>
      )}
    </>
  );
}

function MyLibrariesRow({
  libraries,
}: {
  libraries: HomeLibrary[];
}) {
  return (
    <section className="mb-10" aria-label="My Libraries">
      <div className="mb-3 flex items-end justify-between gap-4">
        <h2 className="text-lg font-semibold tracking-tight">My Libraries</h2>
        <Link
          href="/settings#libraries"
          className="rounded-md px-2 py-1 text-xs font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
        >
          Manage
        </Link>
      </div>
      {libraries.length ? (
        <div className="flex gap-4 overflow-x-auto pb-2 [scrollbar-width:thin]">
          {libraries.map((lib) => (
            <Link
              key={lib.id}
              href={`${lib.library_type === "tv" ? "/shows" : lib.library_type === "movies" ? "/movies" : "/search"}?library_id=${encodeURIComponent(lib.id)}`}
              className="group flex h-24 w-52 shrink-0 flex-col justify-between rounded-xl border border-border bg-card p-4 outline-none transition-colors hover:border-primary/50 hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Library
                className="size-5 text-primary/70 transition-colors group-hover:text-primary"
                aria-hidden="true"
              />
              <span className="line-clamp-2 text-sm font-medium">{lib.name}</span>
            </Link>
          ))}
        </div>
      ) : (
        <p className="rounded-xl border border-dashed border-border/70 p-6 text-sm text-muted-foreground">
          No libraries configured.
        </p>
      )}
    </section>
  );
}
