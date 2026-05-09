"""src.altdata — derived alt-data signals (NDI, ADaS).

Per RULES.md §23 (NDI) and §24 (ADaS). Modules in this package compute
descriptor-level signals on top of the canonical evidence packet; they
do NOT contribute to the packet hash directly (their outputs flow into
agent prompts at runner-call time, not into the packet generator).
"""
