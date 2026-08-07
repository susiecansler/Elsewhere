# The venv lives OUTSIDE the repo on purpose.
#
# This repo sits under ~/Documents, which iCloud Drive syncs. iCloud races
# with the virtualenv and leaves conflict copies ("_editable_impl_elsewhere
# 2.pth"), which intermittently break the editable install with a confusing
# "No module named 'elsewhere'". Keeping the venv out of the synced tree is
# the fix. Override if your checkout isn't in a synced folder.
export UV_PROJECT_ENVIRONMENT ?= $(HOME)/.venvs/elsewhere

UV := uv run --no-progress

.PHONY: help setup test lint fmt check places seeds clean

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  %-10s %s\n", $$1, $$2}'

setup:  ## Create the venv and install dependencies
	uv sync

test:  ## Run the test suite
	$(UV) pytest -q

lint:  ## Lint
	$(UV) ruff check .

fmt:  ## Format
	$(UV) ruff format .

check: lint test  ## Lint and test

places:  ## Build the Overture places table (network, several minutes)
	$(UV) elsewhere places build

seeds:  ## Build the seed corpora and join to Overture
	$(UV) elsewhere seeds build

clean:  ## Remove the venv and derived data
	rm -rf $(UV_PROJECT_ENVIRONMENT) ../data/places
