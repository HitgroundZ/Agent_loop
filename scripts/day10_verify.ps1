$ErrorActionPreference = 'Stop'

function Assert-LastExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

Write-Host '[1/6] Build and start the complete stack'
docker compose up -d --build --wait
Assert-LastExitCode 'docker compose up'

Write-Host '[2/6] Backend integration and hardening tests'
docker compose run --rm backend python -m unittest discover -s tests -v
Assert-LastExitCode 'backend tests'

Write-Host '[3/6] Sandbox unit tests'
docker compose run --rm sandbox-service python -m unittest discover -s tests -v
Assert-LastExitCode 'sandbox unit tests'

Write-Host '[4/6] Real Docker sandbox isolation tests'
docker compose run --rm -e RUN_DOCKER_SANDBOX_INTEGRATION=1 sandbox-service python -m unittest tests.test_docker_integration -v
Assert-LastExitCode 'sandbox integration tests'

Write-Host '[5/6] Frontend production build'
docker compose run --rm frontend-check
Assert-LastExitCode 'frontend build'

Write-Host '[6/6] Live Agent evals'
docker compose run --rm evals
Assert-LastExitCode 'live evals'

Write-Host 'Day 10 verification passed.' -ForegroundColor Green
