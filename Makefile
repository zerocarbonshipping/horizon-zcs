# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

# Horizon is designed to live in the same environment as Navigate, so the
# default environment name matches the one used by navigate-zcs.
ENV_NAME := nav

# Prefer conda if the env exists, otherwise fall back to .venv. RUN must be
# a command prefix, so the venv branch prepends .venv/bin to PATH rather
# than naming the directory itself.
USE_CONDA := $(shell conda env list 2>/dev/null | grep -q "^$(ENV_NAME)[[:space:]]" && echo 1)
ifeq ($(USE_CONDA),1)
  RUN := conda run -n $(ENV_NAME)
else
  RUN := env PATH="$(CURDIR)/.venv/bin:$$PATH"
endif

.PHONY: help conda-setup pip-setup lint test

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

conda-setup:  ## Create conda env with Python 3.12 and install package in editable mode
	@if ! command -v conda >/dev/null 2>&1; then \
		echo "Error: conda is not installed or not on PATH."; \
		exit 1; \
	fi
	@if conda env list | grep -q "^$(ENV_NAME)[[:space:]]"; then \
		echo "Updating existing '$(ENV_NAME)' environment..."; \
	else \
		echo "Creating '$(ENV_NAME)' environment..."; \
		conda create -n $(ENV_NAME) -c conda-forge python=3.12 pip -y; \
	fi
	@conda run -n $(ENV_NAME) pip install -e ".[dev]" --quiet

pip-setup:  ## Create venv and install package with dev dependencies (no conda required)
	@if [ ! -d .venv ]; then \
		echo "Creating virtual environment..."; \
		python3.12 -m venv .venv; \
	fi
	@.venv/bin/pip install -q -e ".[dev]"

lint:  ## Run flake8 and isort checks
	$(RUN) flake8 horizon tests
	$(RUN) isort horizon tests --check-only --diff

test:  ## Run the test suite
	$(RUN) pytest
