# Benchmark data

- `synthetic/` is the **test set**, reported in the README. Generate it with `python -m bench.generate_synthetic --seed 7`.
  The manifests (ground truth) are committed. The images are git-ignored. Re-running the generator reproduces
  every document, its ground truth and its layout exactly, but the scan noise in the scanned/hard tiers comes from
  Pillow's unseeded noise generator, so those pixels differ slightly between generations.
- `dev/` is the **dev set** (seed 1), used for tuning prompts and matchers. It is never reported. Generate it with
  `python -m bench.generate_synthetic --seed 1 --out bench/data/dev`.
- `real_fake/` holds phone photos of *printed documents containing fake data*, labelled with `python -m bench.label_real`.
  It is reported separately from the synthetic results.

All people, companies, GSTINs, amounts and dates are fake. Drug brand names are real trademarks, used only as vocabulary.
