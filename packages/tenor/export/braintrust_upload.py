"""Optional upload of a built export to Braintrust through its official Python SDK.

Separate from :mod:`tenor.export.braintrust` on purpose: this is the only code that can send
anything, and it runs only behind ``tenor export braintrust --upload``. The SDK is not a
dependency of this repo; it is imported lazily and the upload refuses without it. The API key is
never read here: the SDK takes ``BRAINTRUST_API_KEY`` from the environment itself, and this
module only checks that the variable is set.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Mapping
from typing import Any

from tenor.export.braintrust import Export, check_export

KEY_ENV = "BRAINTRUST_API_KEY"


class UploadRefused(RuntimeError):
    """The upload cannot run (no key or no SDK). Nothing was sent."""


def _load_sdk() -> Any:
    return importlib.import_module("braintrust")


def upload(
    export: Export,
    project: str,
    *,
    env: Mapping[str, str] | None = None,
    load_sdk: Callable[[], Any] = _load_sdk,
) -> str:
    """Send the export's dataset and experiment rows to ``project`` and return the SDK's
    experiment summary as text. Raises :class:`UploadRefused` before any network call if
    ``BRAINTRUST_API_KEY`` is unset or the ``braintrust`` package is not installed."""
    if not (os.environ if env is None else env).get(KEY_ENV, "").strip():
        raise UploadRefused(
            f"{KEY_ENV} is not set: nothing uploaded. The files written by --out are complete "
            "without it; set the variable in your environment only if you want to upload."
        )
    try:
        sdk = load_sdk()
    except ImportError as e:
        raise UploadRefused(
            "the `braintrust` package is not installed: nothing uploaded. It is not a dependency "
            "of this repo; install it yourself (`uv pip install braintrust`) to use --upload."
        ) from e
    check_export(export)
    dataset = sdk.init_dataset(project=project, name=export.dataset_name)
    for d in export.dataset:
        dataset.insert(id=d.id, input=d.input, expected=d.expected, metadata=d.metadata)
    dataset.flush()
    experiment = sdk.init(
        project=project,
        experiment=export.experiment_name,
        dataset=dataset,
        metadata=dict(export.run_metadata),
        tags=["tenor", export.experiment],
    )
    for r in export.rows:
        experiment.log(
            id=r.id,
            input=r.input,
            output=r.output,
            expected=r.expected,
            scores=r.scores,
            metadata=r.metadata,
        )
    return str(experiment.summarize())
