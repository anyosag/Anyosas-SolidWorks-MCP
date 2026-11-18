"""Circuit breaker adapter for SolidWorks operations.

Implements the circuit breaker pattern to prevent cascading failures when SolidWorks
operations fail repeatedly.
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any, TypeVar, cast

from loguru import logger

from .base import (
    AdapterHealth,
    AdapterResult,
    AdapterResultStatus,
    ExtrusionParameters,
    LoftParameters,
    MassProperties,
    RevolveParameters,
    SolidWorksAdapter,
    SolidWorksFeature,
    SolidWorksModel,
    SweepParameters,
)

T = TypeVar("T")


def _to_input_dict(params: Any) -> dict[str, Any]:
    """Convert a Pydantic model or plain dict to a flat dict for SoC logging."""
    if hasattr(params, "model_dump"):
        return cast(dict[str, Any], params.model_dump())
    return cast(dict[str, Any], params) if isinstance(params, dict) else {}


def _counts_as_failure(result: AdapterResult[Any]) -> bool:
    """Whether an ERROR result should count toward its operation's breaker.

    A cancelled SolidWorks dialog, a confirmation, a guard that ran nothing
    and a caller's mistake are answers, not a failing SolidWorks; they count
    as a working round trip (``error_classifier.NOT_BREAKER_FAILURES``).
    Imported here, at call time, because the tools package imports the
    adapters.
    """
    if not result.is_error:
        return True  # a timeout or warning counts, as before
    from ..tools.error_classifier import counts_toward_breaker

    return counts_toward_breaker(result.error or "")


class CircuitState(Enum):
    """Circuit breaker states.

    Attributes:
        CLOSED (Any): The closed value.
        HALF_OPEN (Any): The half open value.
        OPEN (Any): The open value.
    """

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Circuit is open, blocking requests
    HALF_OPEN = "half_open"  # Testing if service is back


class _BreakerBucket:
    """Per-operation circuit breaker state.

    Attributes:
        state (CircuitState): The current state of this bucket.
        failure_count (int): Consecutive failures recorded in ``CLOSED``/``OPEN``.
        last_failure_time (float): ``time.time()`` of the most recent failure.
        half_open_calls (int): Calls let through since entering ``HALF_OPEN``.
    """

    __slots__ = ("state", "failure_count", "last_failure_time", "half_open_calls")

    def __init__(self) -> None:
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: float = 0.0
        self.half_open_calls = 0


class CircuitBreakerAdapter(SolidWorksAdapter):
    """Circuit breaker wrapper for SolidWorks adapters.

    Each named operation (``create_cut_extrude``, ``list_features``, ...) gets
    its own independent breaker bucket, keyed by the ``operation_name`` passed
    to ``_execute_with_circuit_breaker``. A run of failures on one tool (e.g.
    every ``create_cut_extrude`` attempt failing while a wrong argument count
    is being worked out) trips only that tool's breaker — unrelated read-only
    tools like ``list_features`` or ``get_model_info`` keep working. Before
    this, a single shared ``CircuitState`` meant one failing tool blocked
    every other tool on the adapter, including reads, until the shared
    recovery timeout elapsed.

    Args:
        adapter (SolidWorksAdapter | None): Adapter instance used for the operation.
                                            Defaults to None.
        failure_threshold (int): The failure threshold value. Defaults to 5.
        recovery_timeout (int): The recovery timeout value. Defaults to 60.
        half_open_max_calls (int): The half open max calls value. Defaults to 3.
        config (dict[str, object] | None): Configuration values for the operation. Defaults
                                           to None.

    Attributes:
        adapter (Any): The adapter value.
        failure_threshold (Any): The failure threshold value.
        half_open_max_calls (Any): The half open max calls value.
        recovery_timeout (Any): The recovery timeout value.
    """

    def __init__(
        self,
        adapter: SolidWorksAdapter | None = None,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        half_open_max_calls: int = 3,
        config: dict[str, object] | None = None,
    ) -> None:
        """Initialize the circuit breaker adapter.

        Args:
            adapter (SolidWorksAdapter | None): Adapter instance used for the operation.
                                                Defaults to None.
            failure_threshold (int): The failure threshold value. Defaults to 5.
            recovery_timeout (int): The recovery timeout value. Defaults to 60.
            half_open_max_calls (int): The half open max calls value. Defaults to 3.
            config (dict[str, object] | None): Configuration values for the operation. Defaults
                                               to None.

        Returns:
            None: None.
        """
        if adapter is None:
            from .mock_adapter import MockSolidWorksAdapter

            adapter = MockSolidWorksAdapter(config or {})
        super().__init__(config)
        self.adapter = adapter
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        # Legacy/default bucket, used by connect() and the generic call() API.
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: float = 0.0
        self.half_open_calls = 0

        # Per-operation buckets, keyed by operation_name, used by every named
        # method that goes through _execute_with_circuit_breaker.
        self._buckets: dict[str, _BreakerBucket] = {}

    def _get_bucket(self, operation_name: str) -> _BreakerBucket:
        """Get (creating if needed) the breaker bucket for one operation name.

        Args:
            operation_name (str): The operation name value.

        Returns:
            _BreakerBucket: That operation's independent breaker state.
        """
        bucket = self._buckets.get(operation_name)
        if bucket is None:
            bucket = _BreakerBucket()
            self._buckets[operation_name] = bucket
        return bucket

    async def _invoke_with_optional_args(
        self,
        method: Callable[..., Awaitable[T]],
        *args: object,
    ) -> T:
        """Invoke adapter method with args, retrying without args on signature mismatch.

        Args:
            method (Callable[..., Awaitable[T]]): The method value.
            *args (object): Additional positional arguments forwarded to the call.

        Returns:
            T: The result produced by the operation.
        """
        try:
            return await method(*args)
        except TypeError:
            return await method()

    def _should_allow_request(self, bucket: Any = None) -> bool:
        """Check if request should be allowed through circuit breaker.

        Args:
            bucket (Any): The breaker bucket to check. Defaults to ``self``
                (the legacy/default bucket used by ``connect()``/``call()``).

        Returns:
            bool: True if should allow request, otherwise False.
        """
        if bucket is None:
            bucket = self
        if bucket.state == CircuitState.CLOSED:
            return True
        elif bucket.state == CircuitState.OPEN:
            # Check if enough time has passed to try again
            if time.time() - bucket.last_failure_time >= self.recovery_timeout:
                bucket.state = CircuitState.HALF_OPEN
                bucket.half_open_calls = 0
                return True
            return False
        elif bucket.state == CircuitState.HALF_OPEN:
            return bucket.half_open_calls < self.half_open_max_calls

    def _record_success(self, bucket: Any = None) -> None:
        """Record successful operation.

        Args:
            bucket (Any): The breaker bucket to update. Defaults to ``self``.

        Returns:
            None: None.
        """
        if bucket is None:
            bucket = self
        if bucket.state == CircuitState.HALF_OPEN:
            # Reset circuit breaker on success in half-open state
            bucket.state = CircuitState.CLOSED
            bucket.failure_count = 0
            bucket.half_open_calls = 0
        elif bucket.state == CircuitState.CLOSED:
            # Reset failure count on success
            bucket.failure_count = 0

    def _record_failure(self, bucket: Any = None) -> None:
        """Record failed operation.

        Args:
            bucket (Any): The breaker bucket to update. Defaults to ``self``.

        Returns:
            None: None.
        """
        if bucket is None:
            bucket = self
        bucket.failure_count += 1
        bucket.last_failure_time = time.time()

        if bucket.state == CircuitState.HALF_OPEN:
            # Go back to open state
            bucket.state = CircuitState.OPEN
        elif (
            bucket.state == CircuitState.CLOSED
            and bucket.failure_count >= self.failure_threshold
        ):
            # Open circuit breaker
            bucket.state = CircuitState.OPEN
            logger.warning(
                f"Circuit breaker opened after {bucket.failure_count} failures"
            )

    async def _execute_with_circuit_breaker(
        self,
        operation_name: str,
        operation: Callable[[], Awaitable[AdapterResult[T]]],
        input_dict: dict[str, Any] | None = None,
        error_results_are_failures: bool = True,
    ) -> AdapterResult[T]:
        """Build internal execute with circuit breaker.

        Args:
            operation_name (str): The operation name value.
            operation (Callable[[], Awaitable[AdapterResult[T]]]): Callable object executed by
                                                                   the helper.
            input_dict (dict | None): Input parameters for SoC logging. Defaults to None.
            error_results_are_failures (bool): When False, an ERROR result
                returned by the operation counts as a healthy round trip, not a
                breaker failure - only exceptions trip the breaker. For
                diagnostic/lookup operations whose ERROR is an expected answer
                ("the model has rebuild errors", "no such feature"); otherwise
                they would lock themselves out exactly when needed.

        Returns:
            AdapterResult[T]: The result produced by the operation.
        """
        bucket = self._get_bucket(operation_name)
        if not self._should_allow_request(bucket):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Circuit breaker is {bucket.state.value} for {operation_name}",
                metadata={"circuit_state": bucket.state.value},
            )

        if bucket.state == CircuitState.HALF_OPEN:
            bucket.half_open_calls += 1

        # Keep the settle gap ahead of any COM call the adapter method makes
        # before its own _handle_com_operation (see PyWin32Adapter.pace_before_com).
        # Looked up on the class so a test double's auto-attributes don't count.
        pace = getattr(type(self.adapter), "pace_before_com", None)
        if callable(pace):
            pace(self.adapter)

        t0 = time.time()
        try:
            result = await operation()
            latency_ms = (time.time() - t0) * 1000.0
            if (
                result.is_success
                or not error_results_are_failures
                or not _counts_as_failure(result)
            ):
                self._record_success(bucket)
            else:
                self._record_failure(bucket)
            self._soc_log(operation_name, input_dict, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - t0) * 1000.0
            self._record_failure(bucket)
            err_result: AdapterResult[T] = AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Circuit breaker caught exception in {operation_name}: {e}",
                metadata={"circuit_state": bucket.state.value},
            )
            self._soc_log(operation_name, input_dict, err_result, latency_ms)
            return err_result

    def _soc_log(
        self,
        tool_name: str,
        input_dict: dict[str, Any] | None,
        result: AdapterResult[Any],
        latency_ms: float,
    ) -> None:
        """Write a ToolCallRecord if soc_session_id is set."""
        if not self.soc_session_id or input_dict is None:
            return
        try:
            from solidworks_mcp.agents.history_db import insert_tool_call_record

            output_data: dict[str, Any] = {}
            if result.data is not None:
                output_data = {"data": result.data}

            insert_tool_call_record(
                session_id=self.soc_session_id,
                tool_name=tool_name,
                input_json=json.dumps(input_dict, default=str),
                output_json=json.dumps(output_data, default=str),
                success=result.is_success,
                latency_ms=latency_ms,
                db_path=self.soc_db_path,
            )
        except Exception as exc:
            logger.debug(
                f"[soc_log] failed to write ToolCallRecord for {tool_name}: {exc}"
            )

    # Adapter interface implementation

    def reset(self, operation: str | None = None) -> list[str]:
        """Close the breaker for one operation, or for all of them.

        Args:
            operation: The operation to reset; ``None`` resets every one.

        Returns:
            list[str]: The operations that were open or half-open.
        """
        if operation is None:
            buckets = dict(self._buckets)
        else:
            buckets = (
                {operation: self._buckets[operation]}
                if operation in self._buckets
                else {}
            )
        was_open = [
            name
            for name, bucket in buckets.items()
            if bucket.state != CircuitState.CLOSED
        ]
        for bucket in buckets.values():
            bucket.state = CircuitState.CLOSED
            bucket.failure_count = 0
            bucket.half_open_calls = 0
        if operation is None:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.half_open_calls = 0
        return sorted(was_open)

    async def reset_circuit_breakers(
        self, operation: str | None = None
    ) -> AdapterResult[dict[str, list[str]]]:
        """Reset breakers through the adapter interface (see :meth:`reset`).

        Args:
            operation: The operation to reset; ``None`` resets every one.

        Returns:
            AdapterResult[dict[str, list[str]]]: ``reset``, the operations that were open.
        """
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS, data={"reset": self.reset(operation)}
        )

    async def set_window_state(self, state: str) -> AdapterResult[dict[str, Any]]:
        """Minimize or restore SolidWorks; not counted by the breaker.

        It changes the window, not the model, so a failure says nothing about
        whether SolidWorks operations should be paused.

        Args:
            state: ``minimize`` or ``restore``.

        Returns:
            AdapterResult[dict[str, Any]]: ``window_state`` afterwards.
        """
        return await self.adapter.set_window_state(state)

    async def connect(self) -> None:
        """Connect through circuit breaker.

        Returns:
            None: None.

        Raises:
            Exception: If the operation cannot be completed.
        """
        if not self._should_allow_request():
            raise Exception(f"Circuit breaker is {self.state.value}")

        try:
            await self.adapter.connect()
            self._record_success()
        except Exception:
            self._record_failure()
            raise

    async def disconnect(self) -> None:
        """Disconnect - always allowed.

        Returns:
            None: None.
        """
        await self.adapter.disconnect()

    def is_connected(self) -> bool:
        """Check connection status.

        Returns:
            bool: True if connected, otherwise False.
        """
        return self.adapter.is_connected()

    def is_application_running(self) -> bool:
        """Forward the wrapped adapter's running check (True when it has none).

        Returns:
            bool: Whether the application is running.
        """
        probe = getattr(type(self.adapter), "is_application_running", None)
        return True if not callable(probe) else bool(probe(self.adapter))

    async def health_check(self) -> AdapterHealth:
        """Get health check with circuit breaker status.

        ``circuit_breaker`` reports the legacy/default bucket (used by
        ``connect()``), which is what "connection health" means here.
        ``circuit_breaker_operations`` reports every per-tool bucket, so a
        tripped tool-specific breaker is visible even while the connection
        itself is healthy.

        Returns:
            AdapterHealth: The result produced by the operation.
        """
        base_health = await self.adapter.health_check()
        if base_health.metrics is None:
            base_health.metrics = {}
        base_health.metrics["circuit_breaker"] = {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "last_failure_time": self.last_failure_time,
            "half_open_calls": self.half_open_calls,
        }
        base_health.metrics["circuit_breaker_operations"] = {
            name: {
                "state": bucket.state.value,
                "failure_count": bucket.failure_count,
                "last_failure_time": bucket.last_failure_time,
                "half_open_calls": bucket.half_open_calls,
            }
            for name, bucket in self._buckets.items()
        }

        # Consider circuit as unhealthy if the connection breaker or any
        # per-operation breaker is open.
        open_operations = [
            name
            for name, bucket in self._buckets.items()
            if bucket.state == CircuitState.OPEN
        ]
        if self.state == CircuitState.OPEN:
            base_health.healthy = False
            base_health.connection_status = "circuit_breaker_open"
        elif open_operations:
            base_health.healthy = False
            base_health.connection_status = (
                f"circuit_breaker_open_for: {', '.join(sorted(open_operations))}"
            )

        return base_health

    # Model operations

    async def open_model(self, file_path: str) -> AdapterResult[SolidWorksModel]:
        """Open model through circuit breaker.

        Args:
            file_path (str): Path to the target file.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "open_model",
            lambda: self.adapter.open_model(file_path),
            input_dict={"file_path": file_path},
        )

    async def open_model_read_only(
        self, file_path: str
    ) -> AdapterResult[SolidWorksModel]:
        """Open a model read-only through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "open_model_read_only",
            lambda: self.adapter.open_model_read_only(file_path),
            input_dict={"file_path": file_path},
        )

    async def get_feature_definition(self, name: str) -> AdapterResult[dict[str, Any]]:
        """Read a feature's parameters through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "get_feature_definition",
            lambda: self.adapter.get_feature_definition(name),
            input_dict={"name": name},
        )

    async def get_sketch(
        self, name: str, detail: str = "full"
    ) -> AdapterResult[dict[str, Any]]:
        """Read a sketch through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "get_sketch",
            lambda: self.adapter.get_sketch(name, detail),
            input_dict={"name": name, "detail": detail},
        )

    async def list_references(
        self, file_path: str, traverse: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """List a file's references through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "list_references",
            lambda: self.adapter.list_references(file_path, traverse),
            input_dict={"file_path": file_path, "traverse": traverse},
        )

    async def move_document(
        self,
        source: str,
        destination: str,
        referencing: list[str] | None = None,
        search_folders: list[str] | None = None,
        dry_run: bool = True,
    ) -> AdapterResult[dict[str, Any]]:
        """Move a SolidWorks file through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "move_document",
            lambda: self.adapter.move_document(
                source, destination, referencing, search_folders, dry_run
            ),
            input_dict={"source": source, "destination": destination},
        )

    async def close_model(
        self, save: bool = False, overwrite_modified_references: bool = False
    ) -> AdapterResult[Any]:
        """Close model through circuit breaker.

        Args:
            save (bool): The save value. Defaults to False.
            overwrite_modified_references (bool): See ``SolidWorksAdapter.close_model``.

        Returns:
            AdapterResult[Any]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "close_model",
            lambda: self.adapter.close_model(save, overwrite_modified_references),
            input_dict={
                "save": save,
                "overwrite_modified_references": overwrite_modified_references,
            },
        )

    async def save_file(
        self,
        file_path: str | None = None,
        overwrite_modified_references: bool = False,
    ) -> AdapterResult[Any]:
        """Save model through circuit breaker.

        Args:
            file_path (str | None): Path to the target file. Defaults to None.
            overwrite_modified_references (bool): See ``SolidWorksAdapter.save_file``.

        Returns:
            AdapterResult[Any]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "save_file",
            lambda: self.adapter.save_file(file_path, overwrite_modified_references),
            input_dict={
                "file_path": file_path,
                "overwrite_modified_references": overwrite_modified_references,
            },
        )

    async def execute_macro(
        self, params: dict[str, Any]
    ) -> AdapterResult[dict[str, Any]]:
        """Provide execute macro support for the circuit breaker adapter.

        Args:
            params (dict[str, Any]): The params value.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "execute_macro",
            lambda: self.adapter.execute_macro(params),  # type: ignore[attr-defined]
            input_dict=params,
        )

    async def create_part(
        self,
        name: str | None = None,
        units: str | None = None,
        template: str | None = None,
    ) -> AdapterResult[SolidWorksModel]:
        """Create part through circuit breaker.

        Args:
            name (str | None): The name value. Defaults to None.
            units (str | None): The units value. Defaults to None.
            template (str | None): Template file path. Defaults to None.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """

        async def _op() -> AdapterResult[SolidWorksModel]:
            """Build internal op.

            Returns:
                AdapterResult[SolidWorksModel]: The result produced by the operation.
            """
            if template is not None:
                # No zero-arg retry: dropping the template would silently
                # build the document from a different one.
                return await self.adapter.create_part(name, units, template)
            if name is None and units is None:
                return await self.adapter.create_part()
            return await self._invoke_with_optional_args(
                self.adapter.create_part,
                name,
                units,
            )

        return await self._execute_with_circuit_breaker(
            "create_part",
            _op,
            input_dict={"name": name, "units": units, "template": template},
        )

    async def call(self, operation: Callable[[], object | Awaitable[object]]) -> object:
        """Legacy call API used by tests.

        Args:
            operation (Callable[[], object | Awaitable[object]]): Callable object executed by
                                                                  the helper.

        Returns:
            object: The result produced by the operation.

        Raises:
            RuntimeError: If the operation cannot be completed.
            Exception: Circuit breaker is open.
        """
        if not self._should_allow_request():
            raise Exception("Circuit breaker is open")
        try:
            result = operation()
            if asyncio.iscoroutine(result):
                result = await result
            self._record_success()
            return result
        except Exception as exc:
            self._record_failure()
            raise RuntimeError(str(exc)) from exc

    async def create_assembly(
        self, name: str | None = None, template: str | None = None
    ) -> AdapterResult[SolidWorksModel]:
        """Create assembly through circuit breaker.

        Args:
            name (str | None): The name value. Defaults to None.
            template (str | None): Template file path. Defaults to None.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """

        async def _op() -> AdapterResult[SolidWorksModel]:
            """Build internal op.

            Returns:
                AdapterResult[SolidWorksModel]: The result produced by the operation.
            """
            if template is not None:
                return await self.adapter.create_assembly(name, template)
            if name is None:
                return await self.adapter.create_assembly()
            return await self._invoke_with_optional_args(
                self.adapter.create_assembly,
                name,
            )

        return await self._execute_with_circuit_breaker(
            "create_assembly", _op, input_dict={"name": name, "template": template}
        )

    async def create_drawing(
        self, name: str | None = None, template: str | None = None
    ) -> AdapterResult[SolidWorksModel]:
        """Create drawing through circuit breaker.

        Args:
            name (str | None): The name value. Defaults to None.
            template (str | None): Template file path. Defaults to None.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "create_drawing",
            lambda: self.adapter.create_drawing(name, template),
            input_dict={"name": name, "template": template},
        )

    # Feature operations

    async def create_extrusion(
        self, params: ExtrusionParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create extrusion through circuit breaker.

        Args:
            params (ExtrusionParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "create_extrusion",
            lambda: self.adapter.create_extrusion(params),
            input_dict=_to_input_dict(params),
        )

    async def create_cut_extrude(
        self, params: ExtrusionParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create cut-extrude through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_cut_extrude",
            lambda: self.adapter.create_cut_extrude(params),
            input_dict=_to_input_dict(params),
        )

    async def add_fillet(
        self, radius: float, edge_names: list[str]
    ) -> AdapterResult[SolidWorksFeature]:
        """Add fillet through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_fillet",
            lambda: self.adapter.add_fillet(radius, edge_names),
            input_dict={"radius": radius, "edge_names": edge_names},
        )

    async def add_chamfer(
        self,
        distance: float,
        edge_names: list[str],
        angle: float = 45.0,
        flip: bool = False,
    ) -> AdapterResult[SolidWorksFeature]:
        """Add chamfer through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_chamfer",
            lambda: self.adapter.add_chamfer(distance, edge_names, angle, flip),
            input_dict={
                "distance": distance,
                "edge_names": edge_names,
                "angle": angle,
                "flip": flip,
            },
        )

    async def get_selected_entities(self) -> AdapterResult[list[dict[str, Any]]]:
        """Report the current UI selection through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "get_selected_entities",
            lambda: self.adapter.get_selected_entities(),
            input_dict={},
        )

    async def select_feature(
        self, feature_name: str, append: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """Select a named feature through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "select_feature",
            lambda: self.adapter.select_feature(feature_name, append),
            input_dict={"feature_name": feature_name, "append": append},
            error_results_are_failures=False,
        )

    async def select_face_edges(
        self,
        point: list[float] | None = None,
        side: str | None = None,
        plane: str | None = None,
        edge_type: str = "all",
        append: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Multi-select a face's edges through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "select_face_edges",
            lambda: self.adapter.select_face_edges(
                point, side, plane, edge_type, append
            ),
            input_dict={
                "point": point,
                "side": side,
                "plane": plane,
                "edge_type": edge_type,
                "append": append,
            },
            error_results_are_failures=False,
        )

    async def select_edges(
        self, query: dict[str, Any], append: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """Multi-select edges by rule through circuit breaker.

        Like ``select_face_edges``, "no edge matches" is an answer, not a
        fault, so it doesn't count against the breaker.
        """
        return await self._execute_with_circuit_breaker(
            "select_edges",
            lambda: self.adapter.select_edges(query, append),
            input_dict={"query": query, "append": append},
            error_results_are_failures=False,
        )

    async def query_geometry(
        self,
        point_mm: list[float],
        radius_mm: float = 2.0,
        kinds: str = "both",
        limit: int = 10,
        read_all: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """List the faces and edges near a point through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "query_geometry",
            lambda: self.adapter.query_geometry(
                point_mm, radius_mm, kinds, limit, read_all
            ),
            input_dict={
                "point_mm": point_mm,
                "radius_mm": radius_mm,
                "kinds": kinds,
                "limit": limit,
                "read_all": read_all,
            },
        )

    async def set_visibility(
        self,
        names: list[str] | None = None,
        types: list[str] | None = None,
        visible: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Show or hide reference geometry and sketches through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "set_visibility",
            lambda: self.adapter.set_visibility(names, types, visible),
            input_dict={"names": names, "types": types, "visible": visible},
        )

    async def check_wall_thickness(
        self,
        min_wall_mm: float = 0.8,
        max_rays: int = 5000,
        exclude_boxes_mm: list[list[float]] | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Measure wall thickness through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "check_wall_thickness",
            lambda: self.adapter.check_wall_thickness(
                min_wall_mm, max_rays, exclude_boxes_mm
            ),
            input_dict={
                "min_wall_mm": min_wall_mm,
                "max_rays": max_rays,
                "exclude_boxes_mm": exclude_boxes_mm,
            },
        )

    async def check_printability(
        self,
        build_direction: str | list[float] | None,
        support_angle_deg: float = 45.0,
        min_bridge_mm: float = 5.0,
        orientations: int = 0,
        min_feature_mm: float | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Check printability through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "check_printability",
            lambda: self.adapter.check_printability(
                build_direction,
                support_angle_deg,
                min_bridge_mm,
                orientations,
                min_feature_mm,
            ),
            input_dict={
                "build_direction": build_direction,
                "support_angle_deg": support_angle_deg,
                "min_bridge_mm": min_bridge_mm,
                "orientations": orientations,
                "min_feature_mm": min_feature_mm,
            },
        )

    async def run_api_snippet(
        self, code: str, timeout_s: float = 60.0, compile_only: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """Run an API snippet through the circuit breaker.

        A snippet that fails to compile or throws is the snippet's fault, not
        the connection's, so only an exception counts against the breaker.
        """
        return await self._execute_with_circuit_breaker(
            "run_api_snippet",
            lambda: self.adapter.run_api_snippet(code, timeout_s, compile_only),
            input_dict={
                "code": code[:2000],
                "timeout_s": timeout_s,
                "compile_only": compile_only,
            },
            error_results_are_failures=False,
        )

    async def edit_feature(
        self, name: str, changes: dict[str, Any]
    ) -> AdapterResult[dict[str, Any]]:
        """Edit a feature's parameters through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "edit_feature",
            lambda: self.adapter.edit_feature(name, changes),
            input_dict={"name": name, "changes": changes},
        )

    async def set_rollback(
        self, before: str | None = None, after: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Move the rollback bar through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "set_rollback",
            lambda: self.adapter.set_rollback(before, after),
            input_dict={"before": before, "after": after},
        )

    async def edit_sketch(self, name: str) -> AdapterResult[dict[str, Any]]:
        """Open an existing sketch through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "edit_sketch",
            lambda: self.adapter.edit_sketch(name),
            input_dict={"name": name},
        )

    async def delete_sketch_entities(
        self, entity_ids: list[str]
    ) -> AdapterResult[dict[str, Any]]:
        """Delete sketch entities through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "delete_sketch_entities",
            lambda: self.adapter.delete_sketch_entities(entity_ids),
            input_dict={"entity_ids": entity_ids},
        )

    async def delete_all_sketch_entities(self) -> AdapterResult[dict[str, Any]]:
        """Delete every entity of the open sketch through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "delete_all_sketch_entities",
            lambda: self.adapter.delete_all_sketch_entities(),
            input_dict={},
        )

    async def rebuild_model(
        self, top_level_only: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """Force a rebuild through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "rebuild_model",
            lambda: self.adapter.rebuild_model(top_level_only),
            input_dict={"top_level_only": top_level_only},
            error_results_are_failures=False,
        )

    async def create_reference_plane(
        self,
        reference: str,
        offset: float = 0.0,
        angle: float = 0.0,
        flip: bool = False,
        axis: str | None = None,
        face_point: list[float] | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Create reference plane through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_reference_plane",
            lambda: self.adapter.create_reference_plane(
                reference, offset, angle, flip, axis, face_point
            ),
            input_dict={
                "reference": reference,
                "offset": offset,
                "angle": angle,
                "flip": flip,
                "axis": axis,
                "face_point": face_point,
            },
        )

    async def create_axis(self, reference: str) -> AdapterResult[dict[str, Any]]:
        """Create reference axis through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_axis",
            lambda: self.adapter.create_axis(reference),
            input_dict={"reference": reference},
        )

    async def create_axis_through(
        self, point_mm: list[float], second_point_mm: list[float]
    ) -> AdapterResult[dict[str, Any]]:
        """Create a reference axis through two points through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_axis_through",
            lambda: self.adapter.create_axis_through(point_mm, second_point_mm),
            input_dict={"point_mm": point_mm, "second_point_mm": second_point_mm},
        )

    async def create_plane_through(
        self, point_mm: list[float], normal: list[float]
    ) -> AdapterResult[dict[str, Any]]:
        """Create a plane through a point, square to a normal, through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_plane_through",
            lambda: self.adapter.create_plane_through(point_mm, normal),
            input_dict={"point_mm": point_mm, "normal": normal},
        )

    async def plane_axes(self, plane: str) -> AdapterResult[dict[str, Any]]:
        """Read a plane's sketch axes through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "plane_axes",
            lambda: self.adapter.plane_axes(plane),
            input_dict={"plane": plane},
        )

    async def get_open_sketch_axes(self) -> AdapterResult[dict[str, Any]]:
        """Read the open sketch's axes through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "get_open_sketch_axes", lambda: self.adapter.get_open_sketch_axes()
        )

    async def create_reference_point(
        self,
        mode: str,
        x: float,
        y: float,
        z: float,
        distance: float | None = None,
        percent: float | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Create a reference point through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_reference_point",
            lambda: self.adapter.create_reference_point(
                mode, x, y, z, distance, percent
            ),
            input_dict={
                "mode": mode,
                "x": x,
                "y": y,
                "z": z,
                "distance": distance,
                "percent": percent,
            },
        )

    async def mirror_feature(
        self,
        features: list[str],
        mirror_plane: str,
        merge: bool = True,
        mirror_bodies: bool = True,
    ) -> AdapterResult[dict[str, Any]]:
        """Mirror solid bodies or features through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "mirror_feature",
            lambda: self.adapter.mirror_feature(
                features, mirror_plane, merge, mirror_bodies
            ),
            input_dict={
                "features": features,
                "mirror_plane": mirror_plane,
                "merge": merge,
                "mirror_bodies": mirror_bodies,
            },
        )

    async def pattern_circular(
        self,
        features: list[str],
        axis: str,
        count: int,
        angle: float = 360.0,
        equal_spacing: bool = True,
        reverse_direction: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Pattern features around an axis through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "pattern_circular",
            lambda: self.adapter.pattern_circular(
                features, axis, count, angle, equal_spacing, reverse_direction
            ),
            input_dict={
                "features": features,
                "axis": axis,
                "count": count,
                "angle": angle,
                "equal_spacing": equal_spacing,
                "reverse_direction": reverse_direction,
            },
        )

    async def pattern_linear(
        self,
        features: list[str],
        direction: str,
        count: int,
        spacing: float,
        direction2: str | None = None,
        count2: int = 1,
        spacing2: float = 0.0,
        reverse_direction: bool = False,
        reverse_direction2: bool = False,
        bodies: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Pattern features or bodies along directions through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "pattern_linear",
            lambda: self.adapter.pattern_linear(
                features,
                direction,
                count,
                spacing,
                direction2,
                count2,
                spacing2,
                reverse_direction,
                reverse_direction2,
                bodies,
            ),
            input_dict={
                "features": features,
                "direction": direction,
                "count": count,
                "spacing": spacing,
                "direction2": direction2,
                "count2": count2,
                "spacing2": spacing2,
                "reverse_direction": reverse_direction,
                "reverse_direction2": reverse_direction2,
                "bodies": bodies,
            },
        )

    async def create_revolve(
        self, params: RevolveParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create revolve through circuit breaker.

        Args:
            params (RevolveParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "create_revolve",
            lambda: self.adapter.create_revolve(params),
            input_dict=_to_input_dict(params),
        )

    async def create_sweep(
        self, params: SweepParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create sweep through circuit breaker.

        Args:
            params (SweepParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "create_sweep",
            lambda: self.adapter.create_sweep(params),
            input_dict=_to_input_dict(params),
        )

    async def create_loft(
        self, params: LoftParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create loft through circuit breaker.

        Args:
            params (LoftParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "create_loft",
            lambda: self.adapter.create_loft(params),
            input_dict=_to_input_dict(params),
        )

    # Sketch operations

    async def create_sketch(self, plane: str) -> AdapterResult[str]:
        """Create sketch through circuit breaker.

        Args:
            plane (str): The plane value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "create_sketch",
            lambda: self.adapter.create_sketch(plane),
            input_dict={"plane": plane},
        )

    async def add_line(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        """Add line through circuit breaker.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "add_line",
            lambda: self.adapter.add_line(x1, y1, x2, y2),
            input_dict={"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        )

    async def add_centerline(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        """Add centerline through circuit breaker.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "add_centerline",
            lambda: self.adapter.add_centerline(x1, y1, x2, y2),
            input_dict={"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        )

    async def add_circle(
        self, center_x: float, center_y: float, radius: float
    ) -> AdapterResult[str]:
        """Add circle through circuit breaker.

        Args:
            center_x (float): The center x value.
            center_y (float): The center y value.
            radius (float): The radius value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "add_circle",
            lambda: self.adapter.add_circle(center_x, center_y, radius),
            input_dict={"center_x": center_x, "center_y": center_y, "radius": radius},
        )

    async def add_rectangle(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        """Add rectangle through circuit breaker.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "add_rectangle",
            lambda: self.adapter.add_rectangle(x1, y1, x2, y2),
            input_dict={"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        )

    async def add_arc(
        self,
        center_x: float,
        center_y: float,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        direction: str = "ccw",
    ) -> AdapterResult[str]:
        """Add arc through circuit breaker.

        Args:
            center_x (float): Arc center X coordinate.
            center_y (float): Arc center Y coordinate.
            start_x (float): Arc start X coordinate.
            start_y (float): Arc start Y coordinate.
            end_x (float): Arc end X coordinate.
            end_y (float): Arc end Y coordinate.
            direction (str): ``"ccw"`` (default) or ``"cw"``.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "add_arc",
            lambda: self.adapter.add_arc(
                center_x,
                center_y,
                start_x,
                start_y,
                end_x,
                end_y,
                direction,
            ),
            input_dict={
                "center_x": center_x,
                "center_y": center_y,
                "start_x": start_x,
                "start_y": start_y,
                "end_x": end_x,
                "end_y": end_y,
                "direction": direction,
            },
        )

    async def add_sketch_outlines(
        self, outlines: list[dict[str, Any]]
    ) -> AdapterResult[dict[str, Any]]:
        """Draw outlines in the open sketch through circuit breaker.

        Args:
            outlines (list[dict[str, Any]]): The resolved outlines.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "add_sketch_outlines",
            lambda: self.adapter.add_sketch_outlines(outlines),
            input_dict={"outlines": outlines},
        )

    async def add_sketch_text(
        self,
        text: str,
        x: float = 0.0,
        y: float = 0.0,
        height: float | None = None,
        font: str | None = None,
        angle: float = 0.0,
        bold: bool = False,
        italic: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Write text in the open sketch through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_sketch_text",
            lambda: self.adapter.add_sketch_text(
                text, x, y, height, font, angle, bold, italic
            ),
            input_dict={
                "text": text,
                "x": x,
                "y": y,
                "height": height,
                "font": font,
                "angle": angle,
                "bold": bold,
                "italic": italic,
            },
        )

    async def set_sketch_construction(
        self, entity_ids: list[str], construction: bool = True
    ) -> AdapterResult[dict[str, Any]]:
        """Set sketch entities' construction state through circuit breaker.

        Args:
            entity_ids (list[str]): The sketch entity ids.
            construction (bool): True for construction geometry.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "set_sketch_construction",
            lambda: self.adapter.set_sketch_construction(entity_ids, construction),
            input_dict={"entity_ids": entity_ids, "construction": construction},
        )

    async def add_spline(self, points: list[dict[str, float]]) -> AdapterResult[str]:
        """Add spline through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_spline",
            lambda: self.adapter.add_spline(points),
            input_dict={"points": points},
        )

    async def add_polygon(
        self, center_x: float, center_y: float, radius: float, sides: int
    ) -> AdapterResult[str]:
        """Add polygon through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_polygon",
            lambda: self.adapter.add_polygon(center_x, center_y, radius, sides),
            input_dict={
                "center_x": center_x,
                "center_y": center_y,
                "radius": radius,
                "sides": sides,
            },
        )

    async def add_ellipse(
        self,
        center_x: float,
        center_y: float,
        major_axis: float,
        minor_axis: float,
    ) -> AdapterResult[str]:
        """Add ellipse through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_ellipse",
            lambda: self.adapter.add_ellipse(
                center_x, center_y, major_axis, minor_axis
            ),
            input_dict={
                "center_x": center_x,
                "center_y": center_y,
                "major_axis": major_axis,
                "minor_axis": minor_axis,
            },
        )

    async def sketch_linear_pattern(
        self,
        entities: list[str],
        direction_x: float,
        direction_y: float,
        spacing: float,
        count: int,
        add_dimensions: bool = True,
    ) -> AdapterResult[str]:
        """Sketch linear pattern through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "sketch_linear_pattern",
            lambda: self.adapter.sketch_linear_pattern(
                entities, direction_x, direction_y, spacing, count, add_dimensions
            ),
            input_dict={
                "entities": entities,
                "direction_x": direction_x,
                "direction_y": direction_y,
                "spacing": spacing,
                "count": count,
                "add_dimensions": add_dimensions,
            },
        )

    async def sketch_circular_pattern(
        self,
        entities: list[str],
        angle: float,
        count: int,
        center_x: float = 0.0,
        center_y: float = 0.0,
        add_dimensions: bool = True,
    ) -> AdapterResult[str]:
        """Sketch circular pattern through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "sketch_circular_pattern",
            lambda: self.adapter.sketch_circular_pattern(
                entities, angle, count, center_x, center_y, add_dimensions
            ),
            input_dict={
                "entities": entities,
                "angle": angle,
                "count": count,
                "center_x": center_x,
                "center_y": center_y,
                "add_dimensions": add_dimensions,
            },
        )

    async def sketch_mirror(
        self, entities: list[str], mirror_line: str
    ) -> AdapterResult[str]:
        """Sketch mirror through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "sketch_mirror",
            lambda: self.adapter.sketch_mirror(entities, mirror_line),
            input_dict={"entities": entities, "mirror_line": mirror_line},
        )

    async def sketch_offset(
        self,
        entities: list[str],
        offset_distance: float,
        reverse_direction: bool,
    ) -> AdapterResult[str]:
        """Sketch offset through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "sketch_offset",
            lambda: self.adapter.sketch_offset(
                entities, offset_distance, reverse_direction
            ),
            input_dict={
                "entities": entities,
                "offset_distance": offset_distance,
                "reverse_direction": reverse_direction,
            },
        )

    async def add_sketch_constraint(
        self,
        entity1: str,
        entity2: str | None,
        relation_type: str,
        entity3: str | None = None,
    ) -> AdapterResult[str]:
        """Add sketch constraint through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_sketch_constraint",
            lambda: self.adapter.add_sketch_constraint(
                entity1,
                entity2,
                relation_type,
                entity3,
            ),
            input_dict={
                "entity1": entity1,
                "entity2": entity2,
                "relation_type": relation_type,
                "entity3": entity3,
            },
        )

    async def add_sketch_dimension(
        self,
        entity1: str,
        entity2: str | None,
        dimension_type: str,
        value: float,
    ) -> AdapterResult[str]:
        """Add sketch dimension through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_sketch_dimension",
            lambda: self.adapter.add_sketch_dimension(
                entity1,
                entity2,
                dimension_type,
                value,
            ),
            input_dict={
                "entity1": entity1,
                "entity2": entity2,
                "dimension_type": dimension_type,
                "value": value,
            },
        )

    async def check_sketch_fully_defined(
        self, sketch_name: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Check sketch definition status through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "check_sketch_fully_defined",
            lambda: self.adapter.check_sketch_fully_defined(sketch_name),
            input_dict={"sketch_name": sketch_name},
        )

    async def exit_sketch(self) -> AdapterResult[None]:
        """Exit sketch through circuit breaker.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "exit_sketch",
            lambda: self.adapter.exit_sketch(),
            input_dict={},
        )

    # Analysis operations

    async def get_mass_properties(self) -> AdapterResult[MassProperties]:
        """Get mass properties through circuit breaker.

        Returns:
            AdapterResult[MassProperties]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "get_mass_properties",
            lambda: self.adapter.get_mass_properties(),
            input_dict={},
        )

    async def get_model_info(self) -> AdapterResult[dict[str, object]]:
        """Get active model metadata through circuit breaker.

        Returns:
            AdapterResult[dict[str, object]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "get_model_info",
            lambda: self.adapter.get_model_info(),
            input_dict={},
        )

    async def list_features(
        self, include_suppressed: bool = False, max_assembly_depth: int = 2
    ) -> AdapterResult[list[dict[str, object]]]:
        """List model features through circuit breaker.

        Args:
            include_suppressed (bool): The include suppressed value. Defaults to False.
            max_assembly_depth (int): Sub-assembly recursion depth. Defaults to 2.

        Returns:
            AdapterResult[list[dict[str, object]]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "list_features",
            lambda: self.adapter.list_features(include_suppressed, max_assembly_depth),
            input_dict={
                "include_suppressed": include_suppressed,
                "max_assembly_depth": max_assembly_depth,
            },
        )

    async def list_configurations(self) -> AdapterResult[list[str]]:
        """List model configurations through circuit breaker.

        Returns:
            AdapterResult[list[str]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "list_configurations",
            lambda: self.adapter.list_configurations(),
            input_dict={},
        )

    # Export operations

    async def export_image(self, payload: dict) -> AdapterResult[dict]:
        """Export viewport image through circuit breaker.

        Args:
            payload (dict): The payload value.

        Returns:
            AdapterResult[dict]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "export_image",
            lambda: self.adapter.export_image(payload),
            input_dict=payload,
        )

    async def export_file(
        self, file_path: str, format_type: str
    ) -> AdapterResult[None]:
        """Export file through circuit breaker.

        Args:
            file_path (str): Path to the target file.
            format_type (str): The format type value.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "export_file",
            lambda: self.adapter.export_file(file_path, format_type),
            input_dict={"file_path": file_path, "format_type": format_type},
        )

    async def export_stl_with_settings(
        self, file_path: str, settings: dict[str, Any]
    ) -> AdapterResult[dict[str, Any]]:
        """Export STL with mesh settings through circuit breaker.

        Args:
            file_path (str): Path to the ``.stl`` file.
            settings (dict[str, Any]): The mesh settings.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "export_stl_with_settings",
            lambda: self.adapter.export_stl_with_settings(file_path, settings),
            input_dict={"file_path": file_path, "settings": settings},
        )

    # Dimension operations

    async def get_dimension(self, name: str) -> AdapterResult[dict[str, Any]]:
        """Get dimension through circuit breaker.

        Args:
            name (str): The name value.

        Returns:
            AdapterResult[dict[str, Any]]: ``value``, ``units`` and ``kind``.
        """
        return await self._execute_with_circuit_breaker(
            "get_dimension",
            lambda: self.adapter.get_dimension(name),
            input_dict={"name": name},
        )

    async def set_dimension(
        self, name: str, value: float
    ) -> AdapterResult[dict[str, Any]]:
        """Set dimension through circuit breaker.

        Args:
            name (str): The name value.
            value (float): The value value.

        Returns:
            AdapterResult[dict[str, Any]]: ``old_value``, ``new_value``, ``units``
            and ``kind``.
        """
        return await self._execute_with_circuit_breaker(
            "set_dimension",
            lambda: self.adapter.set_dimension(name, value),
            input_dict={"name": name, "value": value},
        )

    async def set_units(self, unit_system: str) -> AdapterResult[dict[str, Any]]:
        """Set document units through circuit breaker.

        Args:
            unit_system (str): One of ``mm``, ``cm``, ``m``, ``in``, ``ft``.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "set_units",
            lambda: self.adapter.set_units(unit_system),
            input_dict={"unit_system": unit_system},
        )

    async def list_open_documents(self) -> AdapterResult[list[dict[str, Any]]]:
        """List open documents through circuit breaker.

        Returns:
            AdapterResult[list[dict[str, Any]]]: The result produced by the
            operation.
        """
        return await self._execute_with_circuit_breaker(
            "list_open_documents",
            lambda: self.adapter.list_open_documents(),
            input_dict={},
        )

    async def activate_document(
        self, title_or_path: str
    ) -> AdapterResult[dict[str, Any]]:
        """Activate an open document through circuit breaker.

        Args:
            title_or_path (str): Title, full path, or file name of an open
                document.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "activate_document",
            lambda: self.adapter.activate_document(title_or_path),
            input_dict={"title_or_path": title_or_path},
        )

    async def use_document(self, title_or_path: str) -> AdapterResult[dict[str, Any]]:
        """Use an open document through circuit breaker.

        Args:
            title_or_path (str): Title, full path, or file name of an open
                document.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "use_document",
            lambda: self.adapter.use_document(title_or_path),
            input_dict={"title_or_path": title_or_path},
        )

    def take_document_notice(self) -> str | None:
        """The wrapped adapter's shared-document warning (no breaker: no COM).

        Returns:
            str | None: The warning, or None.
        """
        return self.adapter.take_document_notice()

    # Assembly operations

    async def insert_component(
        self,
        file_path: str,
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.0,
        fixed: bool | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Insert component through circuit breaker.

        Args:
            file_path (str): Path to the target file.
            x (float): X position in millimetres. Defaults to 0.0.
            y (float): Y position in millimetres. Defaults to 0.0.
            z (float): Z position in millimetres. Defaults to 0.0.
            fixed (bool | None): Fix (True) or float (False) it; None leaves
                SolidWorks' choice (the first component is fixed).

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "insert_component",
            lambda: self.adapter.insert_component(file_path, x, y, z, fixed),
            input_dict={
                "file_path": file_path,
                "x": x,
                "y": y,
                "z": z,
                "fixed": fixed,
            },
        )

    async def set_component_transform(
        self,
        component: str,
        origin_mm: list[float] | None = None,
        rotation_rows: list[list[float]] | None = None,
        fixed: bool | None = None,
        turn_axis: list[float] | None = None,
        turn_pivot_mm: list[float] | None = None,
        turn_deg: float = 0.0,
    ) -> AdapterResult[dict[str, Any]]:
        """Place a component through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "set_component_transform",
            lambda: self.adapter.set_component_transform(
                component,
                origin_mm,
                rotation_rows,
                fixed,
                turn_axis,
                turn_pivot_mm,
                turn_deg,
            ),
            input_dict={
                "component": component,
                "origin_mm": origin_mm,
                "rotation_rows": rotation_rows,
                "fixed": fixed,
                "turn_axis": turn_axis,
                "turn_pivot_mm": turn_pivot_mm,
                "turn_deg": turn_deg,
            },
        )

    async def list_components(self) -> AdapterResult[list[str]]:
        """List components through circuit breaker.

        Returns:
            AdapterResult[list[str]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "list_components",
            lambda: self.adapter.list_components(),
            input_dict={},
        )

    async def get_model_geometry_summary(self) -> AdapterResult[dict[str, Any]]:
        """Summarise the active document through circuit breaker.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "get_model_geometry_summary",
            lambda: self.adapter.get_model_geometry_summary(),
            input_dict={},
        )

    async def get_application_info(self) -> AdapterResult[dict[str, Any]]:
        """Describe the connected SolidWorks through circuit breaker.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "get_application_info",
            lambda: self.adapter.get_application_info(),
            input_dict={},
        )

    async def create_drawing_view(
        self, payload: Any = None
    ) -> AdapterResult[dict[str, Any]]:
        """Create a drawing view through circuit breaker.

        Args:
            payload (Any): Tool payload for the drawing view.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "create_drawing_view",
            lambda: self.adapter.create_drawing_view(payload),
            input_dict=payload if isinstance(payload, dict) else {},
        )

    async def add_drawing_view(
        self, payload: Any = None
    ) -> AdapterResult[dict[str, Any]]:
        """Add a drawing view through circuit breaker.

        Args:
            payload (Any): Tool payload for the drawing view.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "add_drawing_view",
            lambda: self.adapter.add_drawing_view(payload),
            input_dict=payload if isinstance(payload, dict) else {},
        )

    async def create_technical_drawing(
        self, payload: Any = None
    ) -> AdapterResult[dict[str, Any]]:
        """Create standard views through circuit breaker.

        Args:
            payload (Any): Tool payload for the technical drawing.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "create_technical_drawing",
            lambda: self.adapter.create_technical_drawing(payload),
            input_dict=payload if isinstance(payload, dict) else {},
        )

    async def add_note(self, payload: Any = None) -> AdapterResult[dict[str, Any]]:
        """Add a drawing note through circuit breaker.

        Args:
            payload (Any): Tool payload for the note.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "add_note",
            lambda: self.adapter.add_note(payload),
            input_dict=payload if isinstance(payload, dict) else {},
        )

    async def list_drawing_views(self) -> AdapterResult[list[str]]:
        """List drawing views through circuit breaker.

        Returns:
            AdapterResult[list[str]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "list_drawing_views",
            lambda: self.adapter.list_drawing_views(),
            input_dict={},
        )

    async def auto_center_marks(
        self,
        view_name: str,
        mark_holes: bool = True,
        mark_fillets: bool = False,
        mark_slots: bool = True,
    ) -> AdapterResult[dict[str, Any]]:
        """Auto-insert drawing centre marks through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "auto_center_marks",
            lambda: self.adapter.auto_center_marks(
                view_name, mark_holes, mark_fillets, mark_slots
            ),
            input_dict={
                "view_name": view_name,
                "mark_holes": mark_holes,
                "mark_fillets": mark_fillets,
                "mark_slots": mark_slots,
            },
        )

    async def save_body_as_part(
        self, body_name: str, file_path: str, assembly_path: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Save a body to a new part through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "save_body_as_part",
            lambda: self.adapter.save_body_as_part(body_name, file_path, assembly_path),
            input_dict={
                "body_name": body_name,
                "file_path": file_path,
                "assembly_path": assembly_path,
            },
        )

    async def get_save_bodies(
        self, feature_name: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Read Save Bodies features through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "get_save_bodies",
            lambda: self.adapter.get_save_bodies(feature_name),
            input_dict={"feature_name": feature_name},
            error_results_are_failures=False,
        )

    async def edit_save_bodies(
        self, feature_name: str, changes: dict[str, Any]
    ) -> AdapterResult[dict[str, Any]]:
        """Change a Save Bodies feature through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "edit_save_bodies",
            lambda: self.adapter.edit_save_bodies(feature_name, changes),
            input_dict={"feature_name": feature_name, "changes": changes},
        )

    async def resplit_bodies(
        self, feature_name: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Update the Save Bodies files through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "resplit_bodies",
            lambda: self.adapter.resplit_bodies(feature_name),
            input_dict={"feature_name": feature_name},
        )

    async def list_loaded_documents(self) -> AdapterResult[dict[str, Any]]:
        """List loaded documents through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "list_loaded_documents",
            lambda: self.adapter.list_loaded_documents(),
            input_dict={},
            error_results_are_failures=False,
        )

    async def close_leftover_documents(self) -> AdapterResult[dict[str, Any]]:
        """Close leftover documents through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "close_leftover_documents",
            lambda: self.adapter.close_leftover_documents(),
            input_dict={},
        )

    async def list_assembly_parts(self) -> AdapterResult[dict[str, Any]]:
        """List an assembly's parts through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "list_assembly_parts",
            lambda: self.adapter.list_assembly_parts(),
            input_dict={},
            error_results_are_failures=False,
        )

    async def list_bodies(
        self, visible_only: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """List the active part's solid bodies through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "list_bodies",
            lambda: self.adapter.list_bodies(visible_only),
            input_dict={"visible_only": visible_only},
        )

    async def insert_part(
        self,
        file_path: str,
        configuration: str | None = None,
        translate_mm: list[float] | None = None,
        rotate_deg: float = 0.0,
        axis: str = "z",
        rotate_about_mm: list[float] | None = None,
        break_link: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Insert a part's bodies into the active part through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "insert_part",
            lambda: self.adapter.insert_part(
                file_path,
                configuration,
                translate_mm,
                rotate_deg,
                axis,
                rotate_about_mm,
                break_link,
            ),
            input_dict={
                "file_path": file_path,
                "configuration": configuration,
                "translate_mm": translate_mm,
                "rotate_deg": rotate_deg,
                "axis": axis,
                "rotate_about_mm": rotate_about_mm,
                "break_link": break_link,
            },
        )

    async def combine_bodies(
        self, operation: str, main: str, tools: list[str]
    ) -> AdapterResult[dict[str, Any]]:
        """Combine bodies of the active part through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "combine_bodies",
            lambda: self.adapter.combine_bodies(operation, main, tools),
            input_dict={"operation": operation, "main": main, "tools": tools},
        )

    async def move_copy_body(
        self,
        bodies: list[str],
        translate_mm: list[float] | None = None,
        rotate_deg: float = 0.0,
        axis: str = "z",
        rotate_about_mm: list[float] | None = None,
        copy: bool = False,
        copies: int = 1,
    ) -> AdapterResult[dict[str, Any]]:
        """Move or copy bodies of the active part through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "move_copy_body",
            lambda: self.adapter.move_copy_body(
                bodies, translate_mm, rotate_deg, axis, rotate_about_mm, copy, copies
            ),
            input_dict={
                "bodies": bodies,
                "translate_mm": translate_mm,
                "rotate_deg": rotate_deg,
                "axis": axis,
                "rotate_about_mm": rotate_about_mm,
                "copy": copy,
                "copies": copies,
            },
        )

    async def delete_keep_body(
        self, bodies: list[str], keep: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """Delete bodies, or keep only them, through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "delete_keep_body",
            lambda: self.adapter.delete_keep_body(bodies, keep),
            input_dict={"bodies": bodies, "keep": keep},
        )

    async def split_body(
        self,
        plane: str | None = None,
        sketch: str | None = None,
        body: str | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Split bodies along a plane or sketch through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "split_body",
            lambda: self.adapter.split_body(plane, sketch, body),
            input_dict={"plane": plane, "sketch": sketch, "body": body},
        )

    async def body_clearance(
        self, body_a: str, body_b: str
    ) -> AdapterResult[dict[str, Any]]:
        """Measure the gap between two bodies through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "body_clearance",
            lambda: self.adapter.body_clearance(body_a, body_b),
            input_dict={"body_a": body_a, "body_b": body_b},
        )

    async def make_mating_negative(
        self,
        target: str,
        tools: list[str],
        clearance_mm: float,
        directions: int = 32,
        axis: str = "y",
        method: str = "auto",
    ) -> AdapterResult[dict[str, Any]]:
        """Cut a mating negative with clearance through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "make_mating_negative",
            lambda: self.adapter.make_mating_negative(
                target, tools, clearance_mm, directions, axis, method
            ),
            input_dict={
                "target": target,
                "tools": tools,
                "clearance_mm": clearance_mm,
                "directions": directions,
                "axis": axis,
                "method": method,
            },
        )

    async def check_interference(
        self, params: Any = None
    ) -> AdapterResult[dict[str, Any]]:
        """Check interference through circuit breaker.

        Args:
            params (Any): Optional interference-detection settings.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "check_interference",
            lambda: self.adapter.check_interference(params),
            input_dict=params if isinstance(params, dict) else {},
        )

    async def measure_clearance(
        self,
        component_a: str,
        component_b: str,
        coarse: bool = False,
        timeout_s: float | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Measure the clearance between two components through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "measure_clearance",
            lambda: self.adapter.measure_clearance(
                component_a, component_b, coarse, timeout_s
            ),
            input_dict={
                "component_a": component_a,
                "component_b": component_b,
                "coarse": coarse,
                "timeout_s": timeout_s,
            },
        )

    async def rotation_clearance(
        self,
        moving: str,
        other: str,
        axis: list[float],
        pivot_mm: list[float] | None = None,
        range_deg: list[float] | None = None,
        step_deg: float = 5.0,
        tolerance_deg: float = 0.1,
    ) -> AdapterResult[dict[str, Any]]:
        """Sweep a turn for clearance through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "rotation_clearance",
            lambda: self.adapter.rotation_clearance(
                moving, other, axis, pivot_mm, range_deg, step_deg, tolerance_deg
            ),
            input_dict={
                "moving": moving,
                "other": other,
                "axis": axis,
                "pivot_mm": pivot_mm,
                "range_deg": range_deg,
                "step_deg": step_deg,
                "tolerance_deg": tolerance_deg,
            },
        )

    async def volume_by_plane(
        self,
        plane: str | None = None,
        point_mm: list[float] | None = None,
        normal: list[float] | None = None,
        bodies: list[str] | None = None,
        enclosed: bool = False,
        density_g_cm3: float | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Measure volume on each side of a plane through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "volume_by_plane",
            lambda: self.adapter.volume_by_plane(
                plane, point_mm, normal, bodies, enclosed, density_g_cm3
            ),
            input_dict={
                "plane": plane,
                "point_mm": point_mm,
                "normal": normal,
                "bodies": bodies,
                "enclosed": enclosed,
                "density_g_cm3": density_g_cm3,
            },
        )

    async def set_density(
        self, density_g_cm3: float, remove_material: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """Set a part's document density through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "set_density",
            lambda: self.adapter.set_density(density_g_cm3, remove_material),
            input_dict={
                "density_g_cm3": density_g_cm3,
                "remove_material": remove_material,
            },
        )

    async def delete_feature(
        self, name: str, delete_absorbed: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """Delete a feature through circuit breaker.

        Args:
            name (str): Feature or sketch name.
            delete_absorbed (bool): Delete the feature's own sketch with it.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "delete_feature",
            lambda: self.adapter.delete_feature(name, delete_absorbed),
            input_dict={"name": name, "delete_absorbed": delete_absorbed},
        )

    async def suppress_feature(
        self, name: str, suppress: bool = True
    ) -> AdapterResult[dict[str, Any]]:
        """Suppress or unsuppress a feature through circuit breaker.

        Args:
            name (str): Feature name.
            suppress (bool): True to suppress, False to unsuppress.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "suppress_feature",
            lambda: self.adapter.suppress_feature(name, suppress),
            input_dict={"name": name, "suppress": suppress},
        )

    async def rename_feature(
        self, old_name: str, new_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Rename a feature through circuit breaker.

        Args:
            old_name (str): Current feature name.
            new_name (str): New feature name.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "rename_feature",
            lambda: self.adapter.rename_feature(old_name, new_name),
            input_dict={"old_name": old_name, "new_name": new_name},
        )

    async def undo(self, count: int = 1) -> AdapterResult[dict[str, Any]]:
        """Undo through circuit breaker.

        Args:
            count (int): Number of steps to undo.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "undo",
            lambda: self.adapter.undo(count),
            input_dict={"count": count},
        )

    async def set_component_state(
        self,
        component: str,
        fixed: bool | None = None,
        flexible: bool | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Set a component's state through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "set_component_state",
            lambda: self.adapter.set_component_state(component, fixed, flexible),
            input_dict={"component": component, "fixed": fixed, "flexible": flexible},
        )

    async def set_component_visibility(
        self, components: list[str], visible: bool
    ) -> AdapterResult[dict[str, Any]]:
        """Hide or show components through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "set_component_visibility",
            lambda: self.adapter.set_component_visibility(components, visible),
            input_dict={"components": components, "visible": visible},
        )

    async def add_mate(
        self,
        component_a: str,
        component_b: str,
        entity_a: str = "Front Plane",
        entity_b: str = "Front Plane",
        mate_type: str = "coincident",
        alignment: str = "aligned",
        distance: float = 0.0,
        angle: float = 0.0,
        face_a: list[float] | None = None,
        face_b: list[float] | None = None,
        flip: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Add mate through circuit breaker.

        Args:
            component_a (str): First component instance name.
            component_b (str): Second component instance name.
            entity_a (str): Named feature on the first component. Defaults to
                "Front Plane".
            entity_b (str): Named feature on the second component. Defaults to
                "Front Plane".
            mate_type (str): Mate type. Defaults to "coincident".
            alignment (str): Mate alignment. Defaults to "aligned".
            distance (float): Distance in millimetres. Defaults to 0.0.
            angle (float): Angle in degrees. Defaults to 0.0.
            face_a (list[float] | None): A point on a face of the first
                component, in its part coordinates (mm), instead of entity_a.
            face_b (list[float] | None): The same for the second component.
            flip (bool): Put a distance or angle mate's component on the
                other side (the mate's ``FlipDimension``).

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "add_mate",
            lambda: self.adapter.add_mate(
                component_a,
                component_b,
                entity_a,
                entity_b,
                mate_type,
                alignment,
                distance,
                angle,
                face_a,
                face_b,
                flip,
            ),
            input_dict={
                "component_a": component_a,
                "component_b": component_b,
                "entity_a": entity_a,
                "entity_b": entity_b,
                "mate_type": mate_type,
                "alignment": alignment,
                "distance": distance,
                "angle": angle,
                "face_a": face_a,
                "face_b": face_b,
                "flip": flip,
            },
        )

    # SolidWorks-as-Code checkpoint

    async def soc_create_checkpoint(
        self,
        label: str,
        file_path: str,
        *,
        feature_tree: list[dict[str, Any]] | None = None,
    ) -> int | None:
        """Create a named SoC checkpoint after saving the model.

        Records a SoCCheckpoint row and a ModelStateSnapshot.  Call this
        immediately after ``save_file`` to mark a stable rollback point.

        Args:
            label (str): Short human name (e.g. "base-extrude").
            file_path (str): Path of the .sldprt that was just saved.
            feature_tree (list | None): Optional feature list from list_features().

        Returns:
            int | None: The new SoCCheckpoint.id, or None if soc_session_id is unset.
        """
        if not self.soc_session_id:
            return None
        try:
            from solidworks_mcp.agents.history_db import (
                create_soc_checkpoint,
                insert_model_state_snapshot,
                list_tool_call_records,
            )

            records = list_tool_call_records(
                self.soc_session_id, db_path=self.soc_db_path
            )
            last_id = records[-1]["id"] if records else None

            snapshot_id: int | None = None
            if feature_tree is not None:
                import json as _json

                insert_model_state_snapshot(
                    session_id=self.soc_session_id,
                    model_path=file_path,
                    feature_tree_json=_json.dumps(feature_tree, default=str),
                    db_path=self.soc_db_path,
                )
                from solidworks_mcp.agents.history_db import list_model_state_snapshots

                snaps = list_model_state_snapshots(
                    self.soc_session_id, db_path=self.soc_db_path
                )
                if snaps:
                    snapshot_id = snaps[0]["id"]

            return create_soc_checkpoint(
                session_id=self.soc_session_id,
                label=label,
                file_path=file_path,
                last_record_id=last_id,
                snapshot_id=snapshot_id,
                db_path=self.soc_db_path,
            )
        except Exception as exc:
            logger.debug(
                f"[soc_checkpoint] failed to create checkpoint {label!r}: {exc}"
            )
            return None

    # --- Engineering-data and drawing-documentation methods ---

    async def list_model_dimensions(
        self,
        feature_name: str | None = None,
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List every dimension in the active part or assembly through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "list_model_dimensions",
            lambda: self.adapter.list_model_dimensions(feature_name),
            input_dict={"feature_name": feature_name},
            error_results_are_failures=False,
        )

    async def list_equations(self) -> AdapterResult[dict[str, Any]]:
        """List the global variables and equations through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "list_equations",
            lambda: self.adapter.list_equations(),
            error_results_are_failures=False,
        )

    async def set_global_variable(
        self, name: str, value: float, units: str = "mm"
    ) -> AdapterResult[dict[str, Any]]:
        """Add or change a global variable through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "set_global_variable",
            lambda: self.adapter.set_global_variable(name, value, units),
            input_dict={"name": name, "value": value, "units": units},
        )

    async def add_equation(
        self, dimension: str, expression: str
    ) -> AdapterResult[dict[str, Any]]:
        """Drive a dimension by an equation through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_equation",
            lambda: self.adapter.add_equation(dimension, expression),
            input_dict={"dimension": dimension, "expression": expression},
        )

    async def delete_equation(
        self, name: str | None = None, index: int | None = None, force: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """Delete an equation or global variable through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "delete_equation",
            lambda: self.adapter.delete_equation(name, index, force),
            input_dict={"name": name, "index": index, "force": force},
        )

    async def measure_entities(
        self,
        entities: list[str],
    ) -> AdapterResult[dict[str, Any]]:
        """Measure one or two selected entities through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "measure_entities",
            lambda: self.adapter.measure_entities(entities),
            input_dict={"entities": entities},
            error_results_are_failures=False,
        )

    async def get_material(
        self,
        configuration: str | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Read the material assigned to the active part through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "get_material",
            lambda: self.adapter.get_material(configuration),
            input_dict={"configuration": configuration},
            error_results_are_failures=False,
        )

    async def set_material(
        self,
        material_name: str,
        database: str | None = None,
        configuration: str | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Assign a material to the active part through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "set_material",
            lambda: self.adapter.set_material(material_name, database, configuration),
            input_dict={
                "material_name": material_name,
                "database": database,
                "configuration": configuration,
            },
        )

    async def list_available_materials(
        self,
        search: str | None = None,
    ) -> AdapterResult[list[dict[str, str]]]:
        """List materials from the configured material libraries through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "list_available_materials",
            lambda: self.adapter.list_available_materials(search),
            input_dict={"search": search},
            error_results_are_failures=False,
        )

    async def get_custom_properties(
        self,
        configuration: str | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Read the custom properties of the active document through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "get_custom_properties",
            lambda: self.adapter.get_custom_properties(configuration),
            input_dict={"configuration": configuration},
            error_results_are_failures=False,
        )

    async def set_custom_property(
        self,
        name: str,
        value: str,
        configuration: str | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Create or overwrite one custom property through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "set_custom_property",
            lambda: self.adapter.set_custom_property(name, value, configuration),
            input_dict={"name": name, "value": value, "configuration": configuration},
        )

    async def export_flat_pattern(
        self,
        file_path: str,
    ) -> AdapterResult[dict[str, Any]]:
        """Export the active sheet-metal part's flat pattern through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "export_flat_pattern",
            lambda: self.adapter.export_flat_pattern(file_path),
            input_dict={"file_path": file_path},
        )

    async def insert_model_dimensions(
        self,
        view_name: str | None = None,
        include_hole_wizard: bool = True,
    ) -> AdapterResult[dict[str, Any]]:
        """Bring model dimensions into drawing views through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "insert_model_dimensions",
            lambda: self.adapter.insert_model_dimensions(
                view_name, include_hole_wizard
            ),
            input_dict={
                "view_name": view_name,
                "include_hole_wizard": include_hole_wizard,
            },
        )

    async def arrange_dimensions(
        self,
        view_name: str | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Move drawing dimensions so none overlaps, then verify through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "arrange_dimensions",
            lambda: self.adapter.arrange_dimensions(view_name),
            input_dict={"view_name": view_name},
        )

    async def insert_bom_table(
        self,
        view_name: str,
        template_path: str | None = None,
        bom_type: str = "top_level_only",
        x_mm: float | None = None,
        y_mm: float | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Insert a BOM table anchored to an assembly drawing view through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "insert_bom_table",
            lambda: self.adapter.insert_bom_table(
                view_name, template_path, bom_type, x_mm, y_mm
            ),
            input_dict={
                "view_name": view_name,
                "template_path": template_path,
                "bom_type": bom_type,
                "x_mm": x_mm,
                "y_mm": y_mm,
            },
        )

    async def set_sheet_scale(
        self,
        numerator: float,
        denominator: float,
    ) -> AdapterResult[dict[str, Any]]:
        """Set the active drawing sheet's scale through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "set_sheet_scale",
            lambda: self.adapter.set_sheet_scale(numerator, denominator),
            input_dict={"numerator": numerator, "denominator": denominator},
        )

    async def add_gtol(
        self,
        characteristic: str,
        tolerance: float,
        x_mm: float,
        y_mm: float,
        diameter_zone: bool = False,
        material_condition: str = "none",
        datums: list[str] | None = None,
        attach_at_mm: tuple[float, float] | None = None,
        dimension: tuple[str, str] | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Add a GD&T feature control frame through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_gtol",
            lambda: self.adapter.add_gtol(
                characteristic,
                tolerance,
                x_mm,
                y_mm,
                diameter_zone,
                material_condition,
                datums,
                attach_at_mm,
                dimension,
            ),
            input_dict={
                "characteristic": characteristic,
                "tolerance": tolerance,
                "x_mm": x_mm,
                "y_mm": y_mm,
                "diameter_zone": diameter_zone,
                "material_condition": material_condition,
                "datums": datums,
                "attach_at_mm": attach_at_mm,
                "dimension": dimension,
            },
        )

    async def add_datum_tag(
        self,
        label: str,
        attach_at_mm: tuple[float, float],
        x_mm: float,
        y_mm: float,
    ) -> AdapterResult[dict[str, Any]]:
        """Add a datum feature symbol through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_datum_tag",
            lambda: self.adapter.add_datum_tag(label, attach_at_mm, x_mm, y_mm),
            input_dict={
                "label": label,
                "attach_at_mm": attach_at_mm,
                "x_mm": x_mm,
                "y_mm": y_mm,
            },
        )

    async def create_standard_drawing(
        self,
        model_path: str,
        output_dir: str | None = None,
        title: str | None = None,
        subtitle: str | None = None,
        drawing_number: str | None = None,
        author: str = "",
        revision: str = "A",
        company: str = "",
        drawn_date: str | None = None,
        notes: list[str] | None = None,
        include_isometric: bool = True,
        write_model_properties: bool = True,
        title_block_text_height_mm: float | None = None,
        include_bom: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Create a complete standard A3 drawing through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_standard_drawing",
            lambda: self.adapter.create_standard_drawing(
                model_path,
                output_dir,
                title,
                subtitle,
                drawing_number,
                author,
                revision,
                company,
                drawn_date,
                notes,
                include_isometric,
                write_model_properties,
                title_block_text_height_mm,
                include_bom,
            ),
            input_dict={
                "model_path": model_path,
                "output_dir": output_dir,
                "title": title,
                "subtitle": subtitle,
                "drawing_number": drawing_number,
                "author": author,
                "revision": revision,
                "company": company,
                "drawn_date": drawn_date,
                "notes": notes,
                "include_isometric": include_isometric,
                "write_model_properties": write_model_properties,
                "title_block_text_height_mm": title_block_text_height_mm,
                "include_bom": include_bom,
            },
        )


class CircuitBreaker:
    """Legacy standalone circuit breaker class expected by tests.

    Args:
        failure_threshold (int): The failure threshold value. Defaults to 5.
        recovery_timeout (float): The recovery timeout value. Defaults to 60.0.
        expected_exception (type[Exception]): The expected exception value. Defaults to
                                              Exception.

    Attributes:
        expected_exception (Any): The expected exception value.
        failure_count (Any): The failure count value.
        failure_threshold (Any): The failure threshold value.
        last_failure_time (Any): The last failure time value.
        recovery_timeout (Any): The recovery timeout value.
        state (Any): The state value.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exception: type[Exception] = Exception,
    ) -> None:
        """Initialize the circuit breaker.

        Args:
            failure_threshold (int): The failure threshold value. Defaults to 5.
            recovery_timeout (float): The recovery timeout value. Defaults to 60.0.
            expected_exception (type[Exception]): The expected exception value. Defaults to
                                                  Exception.

        Returns:
            None: None.
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0

    async def call(self, operation: Callable[[], object | Awaitable[object]]) -> object:
        """Provide call support for the circuit breaker.

        Args:
            operation (Callable[[], object | Awaitable[object]]): Callable object executed by
                                                                  the helper.

        Returns:
            object: The result produced by the operation.

        Raises:
            Exception: Circuit breaker is open.
        """
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time < self.recovery_timeout:
                raise Exception("Circuit breaker is open")
            self.state = CircuitState.HALF_OPEN

        try:
            result = operation()
            if asyncio.iscoroutine(result):
                result = await result
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            return result
        except self.expected_exception:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
            raise
