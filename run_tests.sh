#!/bin/bash
# Test runner script for Alexa OAuth2 integration

set -e

cd "$(dirname "$0")"

echo "================================"
echo "Alexa OAuth2 Integration Tests"
echo "================================"
echo

# Activate virtual environment
source venv/bin/activate

# Run tests with coverage
echo "Running tests with coverage..."
pytest tests/components/alexa/ -v --cov=custom_components/alexa --cov-report=term-missing --cov-report=html

echo
echo "================================"
echo "Test Results Summary"
echo "================================"
echo
echo "Coverage report generated in htmlcov/index.html"
echo "Open with: open htmlcov/index.html"
