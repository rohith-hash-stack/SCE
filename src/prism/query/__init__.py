"""Phase G: the unified query layer - a single validated entry contract
(`PrismQuery`), coordinate/bare-name symbol resolution (`locate`), and a
boolean tag-expression evaluator (`tag_filter`) sitting in front of the
real traversal/packing engines (`prism.traversal.continuous_dijkstra`,
`prism.packer.submodular_knapsack`, `prism.surface.build`)."""
