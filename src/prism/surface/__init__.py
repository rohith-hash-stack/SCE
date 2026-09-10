"""v1.1+ Agent Surface: the canonical `<prism_context>` XML envelope.

A standardized, deterministic rendering layer sitting *on top of* whatever
retrieval engine actually produced a packed context (`prism.slicer.
knapsack.ContextKnapsackPacker` today; `prism.packer.submodular_knapsack`
for the causal engine) - `prism.surface.models.ContextPackage` is the one
shared output shape, `prism.surface.renderer.render` the one shared
serialization, `prism.surface.parser.parse_context` its lossless inverse.
"""
