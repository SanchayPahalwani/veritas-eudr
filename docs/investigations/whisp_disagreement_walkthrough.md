# Investigation: how a Whisp disagreement on one plot is worked

This is the hypothesis-elimination method this project uses on a verdict
disagreement, applied to one plot. It is grounded in a real evidence-ledger row from
this engine. It is **not** a real Whisp comparison: with the synthetic CI rasters the
engine reads painted data, so a genuine disagreement requires the real-data refresh
described in `EVIDENCE.md`. The mechanism reasoned about below is the real one.

## The plot

`pt-014`, a coffee point in the Vietnam Central Highlands AOI. Engine verdict:
**more-info-needed**, `boundary_uncertain = true`. The committed evidence-ledger rows
(run them with `assess_plot`):

```
Hansen GFC-2025-v1.13 lossyear  rule=band21_latency  pixel=21.0  cov=1.000  -> more-info-needed | post_cutoff_loss_fraction=1.000000
JRC GFC2020 V3                  rule=band21_latency  pixel=1.0   cov=1.000  -> more-info-needed | inside_2020_forest
ESA WorldCover v200             rule=band21_latency  pixel=0.0   cov=0.000  -> more-info-needed | no_cropland_context
Hansen layer details: post_cutoff_bands=[21], pre_cutoff_bands=[], band21_only=true,
                      loss_ground_ha=0.7827, loss_fraction=1.000
```

On the three axes this plot looks like a textbook **High**: it is inside the 2020
forest baseline (JRC = 1), it has no commodity/cropland context (WorldCover ≠ cropland),
and it has post-cutoff loss covering essentially the whole plot (loss_fraction ≈ 1.0).
The engine still returns more-info-needed. Suppose Whisp returned **High**. Why the gap?

## Hypotheses

**H1 — the fractional-overlap threshold is mis-tuned (the loss is really an edge artifact).**
Ruled out. The post-cutoff loss coverage here is 1.000 (0.7827 ground ha over a ~0.78 ha
plot), not a single straddling edge pixel. This is the opposite of the tiny-plot
tripwire — the loss is not marginal, so the threshold is not what holds the verdict
back. Eliminated.

**H2 — Hansen annual-band latency at the 2020/2021 boundary.**
Confirmed as the cause. The Hansen detail shows `band21_only = true`: every post-cutoff
loss pixel is in band 21 (calendar 2021), the *first* annual band after the
31-Dec-2020 cutoff. Hansen reports the *year of first detection*, with latency — a
clearing in late 2019 or 2020 routinely surfaces in the 2021 band. So a band-21-only
signal cannot be cleanly attributed to post-cutoff deforestation; it is exactly the
boundary that the annual resolution cannot resolve. The engine therefore treats
band-21-only loss as boundary-uncertain and returns more-info-needed even though the
coverage and forest-baseline conditions would otherwise make it High. This is the
false-flag that would wrongly block a smallholder, so the conservative verdict is the
correct one. A Whisp "High" here would most plausibly come from Whisp weighting the
band-21 signal differently, or from corroborating layers this engine does not run.

**H3 — grid misalignment between JRC (10 m) and Hansen (~31 m).**
Plausible contributor, not the driver here. The two layers are both EPSG:4326 but on
different grids, so "inside 2020 forest" (JRC) and "post-cutoff loss" (Hansen) are
sampled on misaligned cells; near a forest edge they can disagree about whether a given
sub-pixel is forest-then-cleared. For `pt-014` both layers read cleanly at full
coverage, so misalignment is not what produces the more-info-needed verdict — but it is
a legitimate, documented source of engine-vs-Whisp disagreement on edge plots, and the
first thing to check on any plot where JRC and Hansen *do* disagree at the boundary.

**H4 — structural: this engine runs ~3 layers, Whisp runs ~200.**
The most likely source of any real, persistent disagreement once H1–H3 are excluded.
Whisp combines far more datasets (multiple commodity models, protected areas, more
land-cover sources). Where this engine says more-info-needed for lack of corroboration,
Whisp may resolve the same plot with a layer this engine simply does not have. That is a
documented expected limitation, not a bug — and the reason the convergence tiering here
credits Whisp/FDaP as the source rather than presenting itself as novel.

## What I cannot fully explain without real data

On synthetic rasters H2 is decisive and reproducible. On *real* Hansen v1.13 data I
cannot yet say, for this coordinate, whether a band-21 detection corresponds to a
genuine 2021 clearing or to latency on a 2019–2020 event — that needs the underlying
imagery or a higher-cadence source, which is outside this system's inputs. Nor can I
predict, without running it, how Whisp's additional ~200 layers would resolve a plot
this engine leaves at more-info-needed. Both are honest boundaries of a three-layer
convergence model, and both are why the verdict here is "needs more info" rather than a
confident call in either direction.
