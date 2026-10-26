"""Report tests script content and audit calls separately, while recording every call."""

from research_assistant.runtime.gateway import FakeGateway


class ReportGateway(FakeGateway):
    def __init__(self, responses, *, audit_responses=None, delay_seconds=0.0):
        super().__init__(responses, delay_seconds=delay_seconds)
        self.audit_responses = list(audit_responses or [])
        self.audit_remaining = []

    @property
    def content_calls(self):
        return [(stage, messages) for stage, messages in self.calls if ".source_audit" not in stage]

    @property
    def audit_calls(self):
        return [(stage, messages) for stage, messages in self.calls if ".source_audit" in stage]

    def complete(self, messages, *, context, stage, max_tokens=None):
        if ".source_audit" not in stage:
            return super().complete(messages, context=context, stage=stage, max_tokens=max_tokens)
        self.calls.append((stage, tuple(messages)))
        self.audit_remaining.append(context.deadline.remaining())
        item = self.audit_responses.pop(0) if self.audit_responses else '{"issues": []}'
        return FakeGateway([item]).complete(
            messages, context=context, stage=stage, max_tokens=max_tokens
        )
