# Makefile for LaTeX document compilation

ifdef ZP_COMMIT_DATE_EPOCH
export SOURCE_DATE_EPOCH ?= $(ZP_COMMIT_DATE_EPOCH)
endif

# Main document name (without .tex extension)
MAIN = main

# LaTeX compiler
LATEX = pdflatex
BIBTEX = bibtex

# LaTeX flags
LATEX_FLAGS = -interaction=nonstopmode -halt-on-error

# Generated files
PDF = $(MAIN).pdf
AUX_FILES = *.aux *.log *.bbl *.blg *.out *.toc *.lof *.lot *.fls *.fdb_latexmk *.synctex*

# Default target
.PHONY: all
all: $(PDF)

# Main compilation rule (using latexmk with error detection)
$(PDF): $(MAIN).tex $(MAIN).bib
	latexmk -pdf -Werror $(MAIN).tex
	@echo "✓ Compilation successful - no undefined references or citations."
	@echo "-> MD5 file checksum: $$(md5sum $(PDF))"


# Compile without bibliography
.PHONY: quick
quick: $(MAIN).tex
	$(LATEX) $(LATEX_FLAGS) $(MAIN).tex

# Clean auxiliary files
.PHONY: clean
clean:
	rm -f $(AUX_FILES)

# Clean everything including PDF
.PHONY: cleanall
cleanall: clean
	rm -f $(PDF)

.PHONY: deploy
deploy: cleanall all
	@echo "-> SOURCE_DATE_EPOCH: $$SOURCE_DATE_EPOCH"
	@echo "-> ZP_COMMIT_SHA: $$ZP_COMMIT_SHA"

# View PDF (Linux)
.PHONY: view
view: $(PDF)
	xdg-open $(PDF) 2>/dev/null || evince $(PDF) 2>/dev/null || echo "No PDF viewer found"

# Manual compilation (without latexmk)
.PHONY: manual
manual: $(MAIN).tex $(MAIN).bib
	$(LATEX) $(LATEX_FLAGS) $(MAIN).tex
	$(BIBTEX) $(MAIN)
	$(LATEX) $(LATEX_FLAGS) $(MAIN).tex
	$(LATEX) $(LATEX_FLAGS) $(MAIN).tex
	@echo "✓ Manual compilation complete."

# Help
.PHONY: help
help:
	@echo "Available targets:"
	@echo "  make          - Compile with latexmk (stops on undefined refs/citations)"
	@echo "  make manual   - Manual compilation (pdflatex + bibtex, no error check)"
	@echo "  make quick    - Quick compile without bibliography"
	@echo "  make clean    - Remove auxiliary files"
	@echo "  make cleanall - Remove all generated files including PDF"
	@echo "  make deploy   - Prepare for deploy/publication"
	@echo "  make view     - Open PDF in viewer"
	@echo "  make help     - Show this help message"
