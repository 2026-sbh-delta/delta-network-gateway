"""Build the AWS collector; boto3 is provided by the Lambda Python runtime."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parent
repository = root.parents[2]
destination = repository / "platform/.runtime/logging/aws-collector.zip"
destination.parent.mkdir(parents=True, exist_ok=True)
with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    archive.write(root / "aws_collector.py", "aws_collector.py")
    archive.write(repository / "services/gcp-log-collector/collector.py", "collector.py")
print(destination)
