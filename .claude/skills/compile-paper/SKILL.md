---
name: compile-paper
description: Use when compiling paper/main.tex (or debugging a LaTeX build failure) on this machine — module load, the full pdflatex/bibtex sequence, success verification, clean-rebuild recovery, and the natbib pdfendlink workaround. See LATEX_COMPILE_GUIDE.md for full detail.
---

# Compiling the paper

Full details in `LATEX_COMPILE_GUIDE.md`; summary:

- No `pdflatex` on default `PATH` — load it with `module load texlive/2017`
  (newest available on this machine; a modern engine is not readily usable
  here — see the guide if that ever needs revisiting).
- Standard build sequence (always do all passes, a single `pdflatex` run is
  never enough with citations/cross-refs):
  ```bash
  cd paper
  module load texlive/2017
  pdflatex -interaction=nonstopmode main.tex
  bibtex main
  pdflatex -interaction=nonstopmode main.tex
  pdflatex -interaction=nonstopmode main.tex
  ```
- `\bibliography{references}` — **no** `.bib` extension (BibTeX appends it
  automatically; the wrong form fails silently with undefined citations).
- Verify success beyond exit code 0:
  ```bash
  grep -a -c "Citation .* undefined" main.log   # want 0
  grep -a -c "Rerun to get" main.log            # want 0
  ls -la main.pdf                               # fresh timestamp
  ```
  Always pass `-a` to `grep` on `.log` files — without it, matches can be
  silently missed.
- If a run crashes partway, `main.aux` can be left corrupted; don't trust the
  next run without a full clean rebuild (`rm -f main.aux main.bbl main.blg
  main.out main.pdf main.log` and rebuild from scratch).
- Known engine bug: a `\pdfendlink` fatal error can occur from natbib citation
  links landing on a page break. Workaround (only if hit) is in the guide —
  disables citation clickability, not the citation text/rendering.
