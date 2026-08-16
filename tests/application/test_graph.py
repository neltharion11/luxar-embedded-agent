from luxar.application.graph import build_graph


def test_graph_compiles_with_expected_business_topology() -> None:
    drawable = build_graph().get_graph()

    assert set(drawable.nodes) == {
        "__start__",
        "analyze_requirement",
        "create_plan",
        "execute_next_step",
        "create_project",
        "build_project",
        "repair_project",
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
        ("__start__", "analyze_requirement", False),
        ("analyze_requirement", "create_plan", True),
        ("analyze_requirement", "request_clarification", True),
        ("create_plan", "execute_next_step", False),
        ("execute_next_step", "create_project", True),
        ("execute_next_step", "build_project", True),
        ("execute_next_step", "completed", True),
        ("execute_next_step", "failed", True),
        ("create_project", "execute_next_step", True),
        ("create_project", "failed", True),
        ("build_project", "execute_next_step", True),
        ("build_project", "repair_project", True),
        ("build_project", "build_project", True),
        ("build_project", "failed", True),
        ("repair_project", "build_project", False),
        ("request_clarification", "__end__", False),
        ("completed", "__end__", False),
        ("failed", "__end__", False),
    }
