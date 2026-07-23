---
name: bioinformatics
description: Design and review sequence, omics, statistical, and computational biology analyses. Use for pipelines, QC, alignment, annotation, differential analysis, phylogenetics, and reproducible data workflows.
---

# Bioinformatics

Start with the biological question, sampling design, assay, raw data shape, and
target estimand. Inspect metadata and quality before selecting a pipeline.

Record:

- sample and feature identifiers, groups, covariates, and repeated measures;
- reference assembly/database and version;
- tool versions, parameters, random seeds, and compute environment;
- filtering, normalization, transformations, exclusions, and missingness;
- train/validation/test boundaries where models are involved.

Treat contamination, batch effects, compositionality, multiple testing,
population structure, leakage, and pseudoreplication as first-class failure
modes. Prefer effect sizes and uncertainty alongside significance. Confirm that
the statistical unit matches the biological replicate.

Keep raw data immutable and produce a machine-readable run manifest plus
intermediate QC artifacts. Make every derived table traceable to inputs and a
command, script, or notebook cell.
