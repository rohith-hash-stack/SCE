"""v1.1 Causal Coupling: pairwise causal edge weights
(`prism.traversal.causal_weights`) and their two structural indicators -
local data-flow provenance (`data_flow_py`/`data_flow_go`/`data_flow_ts`)
and control-flow guards - used to compute a Continuous Dijkstra
topological distance that shortens for causally-coupled edges without
breaking the triangle inequality.
"""
