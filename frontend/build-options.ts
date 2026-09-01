// Rolldown 1.0.1 can replace a dynamic navigation-module namespace with the
// mangled entry-chunk exports. Preserve that namespace for vinext Link routing.
export const clientBuildOptions = {
  rolldownOptions: {
    experimental: {
      chunkOptimization: { avoidRedundantChunkLoads: false },
    },
  },
};
