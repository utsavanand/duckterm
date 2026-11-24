# Base image for autopilot sandboxes: the harness CLIs, git and Python, run as
# a non-root user. Harness versions are pinned; upgrading one is an image
# change (autopilot spec §4.6).
FROM node:22-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends git python3 python3-venv ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code@2.1.304 @openai/codex@0.155.1 && npm cache clean --force
USER node
# Codex reaches the model through the gateway's proxy with the run token.
RUN mkdir -p /home/node/.codex && printf '%s\n' \
  'model_provider = "duckterm"' \
  '[model_providers.duckterm]' \
  'name = "DuckTerm model proxy"' \
  'base_url = "http://model-proxy:8080/v1"' \
  'env_key = "DUCKTERM_RUN_TOKEN"' \
  'wire_api = "responses"' > /home/node/.codex/config.toml
RUN git config --global user.name "DuckTerm autopilot" \
 && git config --global user.email "autopilot@duckterm.invalid"
USER root
RUN mkdir /workspace && chown node:node /workspace
USER node
WORKDIR /workspace
