# Compiling LaTeX to PDF on This Machine

Written for this specific machine (IoA Linux system), not general LaTeX advice.

## 1. TeX Live installation

There's no `pdflatex` on the default `PATH`/`/usr/bin`. TeX Live lives under `/opt/ioa/texlive/`, versions 2013, 2014, 2015, 2017 — **2017 is the newest available**, and it's managed via environment modules.

Load it with:
```bash
module load texlive/2017
```
(Check what's currently loaded with `module list`; `2017` is often loaded by default in this environment.)

Or call the binaries directly without loading the module:
```bash
/opt/ioa/texlive/2017/bin/x86_64-linux/pdflatex
/opt/ioa/texlive/2017/bin/x86_64-linux/bibtex
```

**No newer engine is readily usable here.** A `conda-forge` `texlive-core` package (version 2026) can be installed (`mamba create -n texlive-modern -c conda-forge texlive-core`), and the binaries it provides genuinely are TeX Live 2026 — but the package is incomplete for real use: `tlmgr` is broken (missing `TeXLive::TLConfig` perl module), there are no precompiled `.fmt` files, and helper scripts (`mktexlsr.pl`, `mktex.opt`) needed for on-the-fly font generation are missing. Getting `pdflatex` to actually build was possible with manual format-generation (`pdftex -ini ... '*pdflatex.ini'`) and pointing `TEXINPUTS`/`BSTINPUTS` at the old 2017 tree for packages, but it still failed on font metric generation for `newtx`-family fonts. Not worth pursuing unless someone fixes the conda package properly. If a modern engine is ever genuinely needed, the source files for this paper originated in **Overleaf**, whose own compile servers run a complete, current TeX Live — that's the easiest path to a modern compile, but it has to be done by hand in the browser (no automated access to it from here).

## 2. Standard build sequence

Any document with a BibTeX bibliography (`\bibliography{...}`) and cross-references (`\ref`, `\pageref`, `\label`) needs multiple passes — a single `pdflatex` run is never enough:

```bash
pdflatex -interaction=nonstopmode main.tex
bibtex main
pdflatex -interaction=nonstopmode main.tex
pdflatex -interaction=nonstopmode main.tex
```

Repeat the final `pdflatex` step until the log stops warning `Rerun to get cross-references right` — usually 2 passes after `bibtex`, occasionally 3 for documents with lots of cross-references.

## 3. How to actually verify the compile succeeded

**Exit code 0 is not sufficient.** Checklist used to properly verify a build:

1. `main.pdf` exists and has a fresh timestamp.
2. Zero undefined citations: `grep -a -c "Citation .* undefined" main.log` → should print `0`.
3. Zero outstanding rerun warnings: `grep -a -c "Rerun to get" main.log` → should print `0`.

**Always pass `-a` to `grep` when searching `.log` files.** Plain `grep -i "undefined" main.log` can silently return zero matches — no error, no "binary file matches" notice — even when the log clearly contains many `Citation ... undefined` lines, apparently because some byte sequence pdfTeX writes makes grep treat the file as binary. This caused a broken PDF (hundreds of undefined citations) to almost be delivered without noticing. If a `grep` check on a `.log` file comes back suspiciously clean, don't trust it without `-a`, or cross-check with `tr -d '\n' < main.log | grep -o "..."`.

## 4. Known gotchas in this environment

### `\bibliography{name.bib}` vs `\bibliography{name}`
BibTeX automatically appends `.bib` to the argument of `\bibliography{}`. If the source has `\bibliography{references.bib}`, BibTeX looks for `references.bib.bib`, doesn't find it, and fails **silently** — no loud error, just a document full of undefined citations. Always use `\bibliography{references}` (no extension).

### Flaky `\pdfendlink` crash (real pdfTeX 2017 engine bug)
Symptom in the log:
```
! pdfTeX error (ext4): \pdfendlink ended up in different nesting level than \pdfstartlink.
!  ==> Fatal error occurred, no output PDF file produced!
```
This is a genuine bug in this old pdfTeX/hyperref combination (reproduced identically under `lualatex` too). It's triggered when a hyperlink — most often an in-text `natbib` citation link — happens to land exactly on a page-break boundary. Because pagination is sensitive to exact text length, small changes (e.g. citations resolving from short "undefined" placeholders to full author-year text) can shift a page break just enough to trigger or dodge it, which makes it look intermittent between otherwise-identical runs.

This was traced extensively — down to instrumenting the raw `\pdfstartlink`/`\pdfendlink` primitives — and no LaTeX-level nesting violation was found; every link opened and closed correctly and in order. pdfTeX's *internal* shipout-time consistency check still fails regardless, so this looks like a genuine low-level engine bug, not a fixable document defect. No single-line fix was found after trying: removing redundant `{\color{black}}` wrapper groups, `\hypersetup{breaklinks=false}`, `\raggedbottom`, replacing `\url{}` with `\texttt{}`, `\NoHyper`-wrapping the page-range footer, and `\NoHyper`-wrapping individual long citation lists.

**Workaround** (only apply if you actually hit this crash): disable clickable links specifically on citations, right after `\begin{document}`:
```latex
\makeatletter
\renewcommand{\hyper@natlinkstart}[1]{}
\renewcommand{\hyper@natlinkend}{}
\renewcommand{\hyper@natlinkbreak}[2]{}
\makeatother
```
This keeps citation *text* fully correct (proper "(Author et al., Year)" rendering) — it just removes their clickability. Section/figure/table cross-references and the bibliography's own DOI/ADS links are unaffected.

### Aux-file corruption after a crash
If a `pdflatex` run dies with a fatal error partway through, it can leave `main.aux` truncated. The *next* run then reads that stale/incomplete aux file and can show citations as "undefined" even though `bibtex` already resolved them correctly (or vice versa — errors can flip between runs). **Don't trust a single successful-looking run after any prior crash.** Do a full clean rebuild and re-verify with the checklist in section 3.

## 5. Recommended clean-build recipe

```bash
cd /path/to/paper
module load texlive/2017   # if not already loaded

rm -f main.aux main.bbl main.blg main.out main.pdf main.log
pdflatex -interaction=nonstopmode main.tex
bibtex main
pdflatex -interaction=nonstopmode main.tex
pdflatex -interaction=nonstopmode main.tex
pdflatex -interaction=nonstopmode main.tex   # extra pass, cheap insurance

# verify before trusting the result:
grep -a -c "Citation .* undefined" main.log   # want: 0
grep -a -c "Rerun to get" main.log            # want: 0
ls -la main.pdf
```

If any check fails, or a pass reports a fatal error, delete the aux files again and start over from scratch rather than just re-running — don't build on top of a possibly-corrupted aux state.
