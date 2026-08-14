# DeepSeek Structured Output

`DeepSeekJsonClient` and a business Adapter form two separate validation layers. The client proves that the provider returned a nonempty JSON object. A business Adapter then uses Pydantic to prove that the dictionary is a valid LUXAR Domain object.

`DeepSeekRequirementParser` includes `FirmwareRequirement.model_json_schema()` in its system prompt, serializes the user task with `json.dumps`, calls the configured fast model, and validates the returned dictionary with `FirmwareRequirement.model_validate()`.

This separation matters because syntactically valid JSON can still contain an unsupported platform, omit required fields, or use the wrong field types. Such output becomes a sanitized `CapabilityError(category="invalid_schema")`; provider response details do not leak into the workflow error message.

The Adapter depends on `JsonCompletionClient`, not directly on the SDK. Offline tests therefore exercise the real prompt and Domain-validation logic through `FakeJsonCompletionClient` without making a network request.
