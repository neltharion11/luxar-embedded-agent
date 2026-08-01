from luxar.application.graph import build_graph


def test_graph_compiles_with_expected_business_topology() -> None:
    drawable = build_graph().get_graph()

    assert set(drawable.nodes) == {
        "__start__",
        "analyze_requirement",
        "create_plan",
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
        ("create_plan", "build_project", False),
        ("build_project", "completed", True),
        ("build_project", "repair_project", True),
        ("build_project", "build_project", True),
        ("build_project", "failed", True),
        ("repair_project", "build_project", False),
        ("request_clarification", "__end__", False),
        ("completed", "__end__", False),
        ("failed", "__end__", False),
    }
