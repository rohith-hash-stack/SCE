"""Query-to-evidence evaluation (diagnostic only; see README.md).

24 questions on FastAPI and Django - seeded controls, unseeded behavioural,
compound, ambiguous and unanswerable - with gold kept in a separate file, and
stage-by-stage loss accounting for every required symbol and relationship
edge through the actual Arm 5 / Prism pipeline. Nothing here changes Prism's
retrieval; the tracer only records what the existing stages return.
"""
