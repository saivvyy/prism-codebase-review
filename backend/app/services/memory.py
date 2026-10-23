import asyncio
import logging
from typing import Any, Dict, List, Optional
from hindsight_client import Hindsight
from app.core.config import get_settings
from app.core.exceptions import ConfigurationError, HindsightMemoryError
from app.schemas.memory import (
    MemoryItemSchema,
    RecallMemoryResponse,
    ReflectMemoryResponse,
    RetainMemoryResponse,
)

logger = logging.getLogger(__name__)

_UNSET = object()


class HindsightMemoryService:
    """Manages persistent organizational engineering memory via official Hindsight SDK."""

    def __init__(
        self,
        base_url: Any = _UNSET,
        api_key: Any = _UNSET,
        default_bank_id: Any = _UNSET,
    ):
        settings = get_settings()
        self.base_url = settings.HINDSIGHT_BASE_URL if base_url is _UNSET else base_url
        self.api_key = settings.HINDSIGHT_API_KEY if api_key is _UNSET else api_key
        self.default_bank_id = (
            settings.HINDSIGHT_DEFAULT_BANK_ID
            if default_bank_id is _UNSET
            else (default_bank_id or "engineering_memory")
        )
        self._client: Optional[Hindsight] = None
        self._client_loop: Any = None
        self._local_memories: Dict[str, List[MemoryItemSchema]] = {}

    @property
    def is_configured(self) -> bool:
        """Returns True if Hindsight client can be initialized."""
        return bool(self.base_url and self.api_key)

    @property
    def client(self) -> Hindsight:
        """Initializes and returns Hindsight client."""
        if not self.is_configured:
            raise ConfigurationError(
                "Hindsight memory is not configured. HINDSIGHT_API_KEY and HINDSIGHT_BASE_URL must be set."
            )
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._client is None or (current_loop is not None and getattr(self, "_client_loop", None) != current_loop):
            try:
                self._client = Hindsight(
                    base_url=self.base_url,
                    api_key=self.api_key,
                )
                self._client_loop = current_loop
            except Exception as e:
                logger.error("Failed to initialize Hindsight client: %s", e)
                raise HindsightMemoryError(f"Could not initialize Hindsight client: {e}") from e
        return self._client

    def _retain_local(
        self,
        content: str,
        target_bank: str,
        context: Optional[str] = None,
        document_id: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        tags: Optional[List[str]] = None,
    ) -> RetainMemoryResponse:
        import uuid
        item = MemoryItemSchema(
            id=document_id or str(uuid.uuid4()),
            text=content,
            type=tags[0] if tags else "architectural_decision",
            score=1.0,
            metadata=metadata or {},
            tags=tags or [],
        )
        if target_bank not in self._local_memories:
            self._local_memories[target_bank] = []
        self._local_memories[target_bank].append(item)
        return RetainMemoryResponse(
            success=True,
            bank_id=target_bank,
            items_count=1,
            operation_id="local_retain_op",
        )

    def _recall_local(
        self,
        query: str,
        target_bank: str,
        tags: Optional[List[str]] = None,
    ) -> RecallMemoryResponse:
        bank_items = self._local_memories.get(target_bank, [])
        matched: List[MemoryItemSchema] = []
        q_terms = [t.lower().strip() for t in query.split() if len(t.strip()) > 2]
        for it in bank_items:
            if tags and not any(t in (it.tags or []) for t in tags):
                continue
            score = 0.5
            if any(term in it.text.lower() for term in q_terms):
                score = 0.95
            matched.append(
                MemoryItemSchema(
                    id=it.id,
                    text=it.text,
                    type=it.type,
                    score=score,
                    metadata=it.metadata,
                    tags=it.tags,
                )
            )
        prompt_context = "\n".join([f"- {m.text}" for m in matched])
        return RecallMemoryResponse(
            bank_id=target_bank,
            query=query,
            results_count=len(matched),
            items=matched,
            prompt_context=prompt_context,
        )

    def _process_recall_result(self, res: Any, target_bank: str, query: str) -> RecallMemoryResponse:
        items: List[MemoryItemSchema] = []
        results = getattr(res, "results", []) or []
        for r in results:
            items.append(
                MemoryItemSchema(
                    id=getattr(r, "id", None),
                    text=getattr(r, "text", str(r)),
                    type=getattr(r, "type", None),
                    score=getattr(r, "score", None),
                    metadata=getattr(r, "metadata", None),
                    tags=getattr(r, "tags", None),
                )
            )

        prompt_context = ""
        if hasattr(res, "to_prompt_string"):
            try:
                prompt_context = res.to_prompt_string()
            except Exception:
                prompt_context = "\n".join([item.text for item in items])
        else:
            prompt_context = "\n".join([item.text for item in items])

        return RecallMemoryResponse(
            bank_id=target_bank,
            query=query,
            results_count=len(items),
            items=items,
            prompt_context=prompt_context,
        )

    async def aretain(
        self,
        content: str,
        bank_id: Optional[str] = None,
        context: Optional[str] = None,
        document_id: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        tags: Optional[List[str]] = None,
    ) -> RetainMemoryResponse:
        """Asynchronously retains engineering knowledge into Hindsight."""
        target_bank = bank_id or self.default_bank_id
        if not self.is_configured:
            return self._retain_local(content, target_bank, context, document_id, metadata, tags)

        try:
            res = await self.client.aretain(
                bank_id=target_bank,
                content=content,
                context=context,
                document_id=document_id,
                metadata=metadata,
                tags=tags,
            )
            return RetainMemoryResponse(
                success=getattr(res, "success", True),
                bank_id=target_bank,
                items_count=getattr(res, "items_count", 1),
                operation_id=getattr(res, "operation_id", None),
            )
        except ConfigurationError:
            raise
        except Exception as e:
            logger.error("Remote Hindsight aretain failed: %s", e)
            raise HindsightMemoryError(f"Hindsight aretain failed for bank '{target_bank}': {e}") from e

    def retain(
        self,
        content: str,
        bank_id: Optional[str] = None,
        context: Optional[str] = None,
        document_id: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        tags: Optional[List[str]] = None,
    ) -> RetainMemoryResponse:
        """Retains engineering knowledge, rejected patterns, or review decisions into memory."""
        target_bank = bank_id or self.default_bank_id
        if not self.is_configured:
            return self._retain_local(content, target_bank, context, document_id, metadata, tags)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            def _worker():
                c = Hindsight(base_url=self.base_url, api_key=self.api_key)
                return c.retain(
                    bank_id=target_bank,
                    content=content,
                    context=context,
                    document_id=document_id,
                    metadata=metadata,
                    tags=tags,
                )
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    res = ex.submit(_worker).result()
                return RetainMemoryResponse(
                    success=getattr(res, "success", True),
                    bank_id=target_bank,
                    items_count=getattr(res, "items_count", 1),
                    operation_id=getattr(res, "operation_id", None),
                )
            except Exception as e:
                logger.error("Remote Hindsight retain worker failed: %s", e)
                raise HindsightMemoryError(f"Hindsight retain worker failed for bank '{target_bank}': {e}") from e

        try:
            res = self.client.retain(
                bank_id=target_bank,
                content=content,
                context=context,
                document_id=document_id,
                metadata=metadata,
                tags=tags,
            )
            return RetainMemoryResponse(
                success=getattr(res, "success", True),
                bank_id=target_bank,
                items_count=getattr(res, "items_count", 1),
                operation_id=getattr(res, "operation_id", None),
            )
        except ConfigurationError:
            raise
        except Exception as e:
            logger.error("Remote Hindsight retain failed: %s", e)
            raise HindsightMemoryError(f"Hindsight retain failed for bank '{target_bank}': {e}") from e

    async def alist_memories(
        self,
        bank_id: Optional[str] = None,
        category: Optional[str] = None,
        search_query: Optional[str] = None,
        limit: int = 100,
    ) -> List[MemoryItemSchema]:
        """Lists memories from Hindsight bank."""
        target_bank = bank_id or self.default_bank_id
        if not self.is_configured:
            local_items = self._local_memories.get(target_bank, [])
            if category and category != "all":
                return [m for m in local_items if m.type == category or (m.tags and category in m.tags)]
            return local_items

        try:
            res = await self.client.alist_memories(
                bank_id=target_bank,
                search_query=search_query,
                limit=limit,
            )
            raw_items = getattr(res, "items", []) or []
            world_ids = {
                getattr(item, "id", None)
                for item in raw_items
                if getattr(item, "fact_type", None) == "world" or getattr(item, "document_id", None)
            }

            items: List[MemoryItemSchema] = []
            for item in raw_items:
                fact_type = getattr(item, "fact_type", None)
                source_ids = getattr(item, "source_memory_ids", []) or []
                if fact_type == "observation" and any(sid in world_ids for sid in source_ids):
                    continue

                raw_tags = getattr(item, "tags", []) or []
                raw_meta = getattr(item, "metadata", {}) or {}
                raw_type = (
                    raw_meta.get("category")
                    or raw_meta.get("type")
                    or (raw_tags[0] if raw_tags else getattr(item, "fact_type", "architecture_decision"))
                )
                if category and category != "all" and raw_type != category and category not in raw_tags:
                    continue
                items.append(
                    MemoryItemSchema(
                        id=getattr(item, "id", None),
                        text=getattr(item, "text", ""),
                        type=raw_type,
                        metadata=raw_meta,
                        tags=raw_tags,
                    )
                )
            return items
        except Exception as e:
            if "404" in str(e) or "not found" in str(e).lower():
                return []
            logger.error("Hindsight alist_memories failed for bank '%s': %s", target_bank, e)
            raise HindsightMemoryError(f"Hindsight alist_memories failed for bank '{target_bank}': {e}") from e

    def list_memories(
        self,
        bank_id: Optional[str] = None,
        category: Optional[str] = None,
        search_query: Optional[str] = None,
        limit: int = 100,
    ) -> List[MemoryItemSchema]:
        """Lists memories from Hindsight bank synchronously."""
        target_bank = bank_id or self.default_bank_id
        if not self.is_configured:
            local_items = self._local_memories.get(target_bank, [])
            if category and category != "all":
                return [m for m in local_items if m.type == category or (m.tags and category in m.tags)]
            return local_items

        def _process_list_res(res: Any) -> List[MemoryItemSchema]:
            raw_items = getattr(res, "items", []) or []
            world_ids = {
                getattr(item, "id", None)
                for item in raw_items
                if getattr(item, "fact_type", None) == "world" or getattr(item, "document_id", None)
            }

            items: List[MemoryItemSchema] = []
            for item in raw_items:
                fact_type = getattr(item, "fact_type", None)
                source_ids = getattr(item, "source_memory_ids", []) or []
                if fact_type == "observation" and any(sid in world_ids for sid in source_ids):
                    continue

                raw_tags = getattr(item, "tags", []) or []
                raw_meta = getattr(item, "metadata", {}) or {}
                raw_type = (
                    raw_meta.get("category")
                    or raw_meta.get("type")
                    or (raw_tags[0] if raw_tags else getattr(item, "fact_type", "architecture_decision"))
                )
                if category and category != "all" and raw_type != category and category not in raw_tags:
                    continue
                items.append(
                    MemoryItemSchema(
                        id=getattr(item, "id", None),
                        text=getattr(item, "text", ""),
                        type=raw_type,
                        metadata=raw_meta,
                        tags=raw_tags,
                    )
                )
            return items

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            def _worker():
                c = Hindsight(base_url=self.base_url, api_key=self.api_key)
                return c.list_memories(
                    bank_id=target_bank,
                    search_query=search_query,
                    limit=limit,
                )
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    res = ex.submit(_worker).result()
                return _process_list_res(res)
            except Exception as e:
                if "404" in str(e) or "not found" in str(e).lower():
                    return []
                logger.error("Hindsight list_memories thread worker failed for bank '%s': %s", target_bank, e)
                raise HindsightMemoryError(f"Hindsight list_memories thread worker failed for bank '{target_bank}': {e}") from e

        try:
            res = self.client.list_memories(
                bank_id=target_bank,
                search_query=search_query,
                limit=limit,
            )
            return _process_list_res(res)
        except Exception as e:
            if "404" in str(e) or "not found" in str(e).lower():
                return []
            logger.error("Hindsight list_memories failed for bank '%s': %s", target_bank, e)
            raise HindsightMemoryError(f"Hindsight list_memories failed for bank '{target_bank}': {e}") from e

    async def arecall(
        self,
        query: str,
        bank_id: Optional[str] = None,
        max_tokens: int = 4096,
        tags: Optional[List[str]] = None,
    ) -> RecallMemoryResponse:
        """Asynchronously recalls relevant historical architectural decisions from Hindsight."""
        target_bank = bank_id or self.default_bank_id
        if not self.is_configured:
            return self._recall_local(query, target_bank, tags)

        try:
            res = await self.client.arecall(
                bank_id=target_bank,
                query=query,
                max_tokens=max_tokens,
                tags=tags,
            )
            return self._process_recall_result(res, target_bank, query)
        except ConfigurationError:
            raise
        except Exception as e:
            if "404" in str(e) or "not found" in str(e).lower():
                return RecallMemoryResponse(bank_id=target_bank, query=query, results_count=0, items=[], prompt_context="")
            logger.error("Hindsight arecall failed for bank '%s': %s", target_bank, e)
            raise HindsightMemoryError(f"Hindsight arecall failed for bank '{target_bank}': {e}") from e

    def recall(
        self,
        query: str,
        bank_id: Optional[str] = None,
        max_tokens: int = 4096,
        tags: Optional[List[str]] = None,
    ) -> RecallMemoryResponse:
        """Recalls relevant historical architectural decisions and exceptions for a given query/diff."""
        target_bank = bank_id or self.default_bank_id
        if not self.is_configured:
            return self._recall_local(query, target_bank, tags)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            def _worker():
                c = Hindsight(base_url=self.base_url, api_key=self.api_key)
                return c.recall(
                    bank_id=target_bank,
                    query=query,
                    max_tokens=max_tokens,
                    tags=tags,
                )
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    res = ex.submit(_worker).result()
                return self._process_recall_result(res, target_bank, query)
            except Exception as e:
                if "404" in str(e) or "not found" in str(e).lower():
                    return RecallMemoryResponse(bank_id=target_bank, query=query, results_count=0, items=[], prompt_context="")
                logger.error("Hindsight recall thread worker failed for bank '%s': %s", target_bank, e)
                raise HindsightMemoryError(f"Hindsight recall thread worker failed for bank '{target_bank}': {e}") from e

        try:
            res = self.client.recall(
                bank_id=target_bank,
                query=query,
                max_tokens=max_tokens,
                tags=tags,
            )
            return self._process_recall_result(res, target_bank, query)
        except ConfigurationError:
            raise
        except HindsightMemoryError:
            raise
        except Exception as e:
            if "404" in str(e) or "not found" in str(e).lower():
                return RecallMemoryResponse(bank_id=target_bank, query=query, results_count=0, items=[], prompt_context="")
            logger.error("Hindsight recall failed for bank '%s': %s", target_bank, e)
            raise HindsightMemoryError(f"Hindsight recall failed for bank '{target_bank}': {e}") from e

    async def areflect(
        self,
        query: str,
        bank_id: Optional[str] = None,
        context: Optional[str] = None,
        budget: str = "low",
        tags: Optional[List[str]] = None,
    ) -> ReflectMemoryResponse:
        """Asynchronously performs analytical reflection over accumulated memory."""
        target_bank = bank_id or self.default_bank_id
        try:
            res = await self.client.areflect(
                bank_id=target_bank,
                query=query,
                context=context,
                budget=budget,
                tags=tags,
            )
            answer = getattr(res, "text", "") or ""
            based_on = getattr(res, "based_on", None)
            return ReflectMemoryResponse(
                bank_id=target_bank,
                query=query,
                answer=answer,
                based_on=based_on,
            )
        except ConfigurationError:
            raise
        except Exception as e:
            logger.error("Hindsight areflect failed: %s", e)
            raise HindsightMemoryError(f"Failed to reflect memory in bank '{target_bank}': {e}") from e


    def reflect(
        self,
        query: str,
        bank_id: Optional[str] = None,
        context: Optional[str] = None,
        budget: str = "low",
        tags: Optional[List[str]] = None,
    ) -> ReflectMemoryResponse:
        """Performs analytical reflection over accumulated memory."""
        target_bank = bank_id or self.default_bank_id
        try:
            res = self.client.reflect(
                bank_id=target_bank,
                query=query,
                context=context,
                budget=budget,
                tags=tags,
            )
            answer = getattr(res, "text", "") or ""
            based_on = getattr(res, "based_on", None)
            return ReflectMemoryResponse(
                bank_id=target_bank,
                query=query,
                answer=answer,
                based_on=based_on,
            )
        except ConfigurationError:
            raise
        except Exception as e:
            logger.error("Hindsight reflect failed: %s", e)
            raise HindsightMemoryError(f"Failed to reflect memory in bank '{target_bank}': {e}") from e


_hindsight_service: Optional[HindsightMemoryService] = None


def get_hindsight_service() -> HindsightMemoryService:
    """Returns singleton instance of HindsightMemoryService."""
    global _hindsight_service
    if _hindsight_service is None:
        _hindsight_service = HindsightMemoryService()
    return _hindsight_service
