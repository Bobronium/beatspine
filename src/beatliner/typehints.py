
from datetime import datetime
from decimal import Decimal
from pathlib import Path


type Milliseconds = Decimal
type Seconds = Decimal
type FrameCount = int
type PhotoMetadata = tuple[Path, datetime, int | None]
