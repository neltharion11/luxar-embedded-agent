from luxar.application.graph import build_graph


def test_graph_compiles_with_expected_business_topology() -> None:
    drawable = build_graph().get_graph()

    assert set(drawable.nodes) == {
        "__start__",
        "analyze_requirement",
        "analyze_project",
        "report_project",
        "create_plan",
        "execute_next_step",
        "create_project",
        "find_idf_examples",
        "implement_change",
        "build_project",
        "repair_project",
        "request_flash_approval",
        "flash_project",
        "monitor_project",
        "analyze_device_logs",
        "request_clarification",
        "completed",
        "failed",
        "__end__",
    }

    edges = {
        (edge.source, edge.target, edge.conditional)
        for edge in drawable.edges
    }
    assert edges == {
        ("__start__", "analyze_requirement", True),
        ("__start__", "analyze_project", True),
        ("analyze_requirement", "analyze_project", True),
        ("analyze_requirement", "request_clarification", True),
        ("analyze_project", "report_project", True),
        ("analyze_project", "create_project", True),
        ("analyze_project", "create_plan", True),
        ("analyze_project", "failed", True),
        ("report_project", "__end__", False),
        ("create_plan", "execute_next_step", False),
        ("execute_next_step", "create_project", True),
        ("execute_next_step", "find_idf_examples", True),
        ("execute_next_step", "build_project", True),
        ("execute_next_step", "request_flash_approval", True),
        ("execute_next_step", "monitor_project", True),
        ("execute_next_step", "completed", True),
        ("execute_next_step", "failed", True),
        ("create_project", "execute_next_step", True),
        ("create_project", "analyze_project", True),
        ("create_project", "failed", True),
        ("find_idf_examples", "implement_change", False),
        ("implement_change", "execute_next_step", False),
        ("build_project", "execute_next_step", True),
        ("build_project", "request_flash_approval", True),
        ("build_project", "repair_project", True),
        ("build_project", "build_project", True),
        ("build_project", "failed", True),
        ("repair_project", "build_project", False),
        ("request_flash_approval", "flash_project", True),
        ("request_flash_approval", "failed", True),
        ("flash_project", "execute_next_step", True),
        ("flash_project", "monitor_project", True),
        ("flash_project", "flash_project", True),
        ("flash_project", "failed", True),
        ("monitor_project", "analyze_device_logs", False),
        ("analyze_device_logs", "repair_project", True),
        ("analyze_device_logs", "completed", True),
        ("analyze_device_logs", "failed", True),
        ("request_clarification", "__end__", False),
        ("completed", "__end__", False),
        ("failed", "__end__", False),
    }
