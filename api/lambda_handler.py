"""AWS Lambda entry point: the FastAPI app behind a Lambda Function URL, via Mangum.

The image (api/Dockerfile) carries only the API's dependencies; data comes from the public
snapshots over HTTPS (PUBLIC_BASE_URL), so the function needs no AWS permissions beyond logs.
"""

from mangum import Mangum

from api.app import app

handler = Mangum(app, lifespan="off")
