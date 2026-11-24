#!/usr/bin/env bash
# Starts the Lead Finder dashboard (and the scraper behind it). Stop with Ctrl+C.
cd "$(dirname "$0")"
exec python3 dashboard/server.py "$@"
