# Contextual teaching quality

Context is an interpretation aid for short follow-up questions. The worker only reads prior turns for the exact sender, channel, group and class, within the event's trusted server-received 20-minute window. At most five turns and 3,500 characters are considered. A prior turn must be answered, have an `ANSWER_ACK` outbox row marked `SENT`, and cite sources that remain eligible at read time.

The worker stores one `event_context_resolutions` row per event. It contains prior event IDs, a bounded resolved query, its SHA-256 query hash, status, intent and resolver revision; it does not copy prior answers or source text. Replays use the same query. Before persistence, the worker rechecks the current binding and every referenced turn's answer, delivery and source eligibility. The normal retention policy for Events and Answers still controls source record retention; the 20-minute interval only controls context eligibility.

The deterministic resolver carries stable topic identifiers from the latest eligible question and retains the current question verbatim. It does not call an LLM to rewrite queries. A generic reference with multiple possible antecedents, or an elliptical question without history, becomes an `AMBIGUOUS_QUESTION` ticket. New topics discard history. History never enters the evidence catalog: every answer still needs fresh retrieval, the existing evidence gate and exact quote validation.

The answer formatter adds a server-owned generic heading and leaves quote text and citation identity/version untouched. Claim coverage labels compare claims to exact evidence spans. Partial or unsupported coverage is diagnostic and does not authorize a partial student-facing answer. The Answer/Ticket XOR and transactional outbox path remain in force.

Free semantic paraphrasing is not implemented or enabled. The fixture dataset is synthetic and pending independent review; it is not a quality benchmark and makes no claim that semantic understanding has been proven. RAG versus fine-tuning remains a handoff without a reviewed source that supports the comparison.
