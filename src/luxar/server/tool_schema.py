from __future__ import annotations


TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "runtime_run",
            "description": "Run the LUXAR 0.2.2 runtime for a task inside the current workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Natural-language task description"},
                    "project": {"type": "string", "description": "Optional project name"},
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "runtime_explain",
            "description": "Explain the LUXAR 0.2.2 runtime model and current orchestration approach.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skills_list",
            "description": "List available runtime skills, optionally filtered by category.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Optional skill category filter"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_view",
            "description": "View a single runtime skill by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_manage",
            "description": "Create, edit, patch, or archive a runtime skill.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "create, edit, patch, or archive"},
                    "name": {"type": "string", "description": "Skill name"},
                    "category": {"type": "string", "description": "Skill category"},
                    "content": {"type": "string", "description": "Replacement or creation content"},
                    "old_string": {"type": "string", "description": "Patch target text"},
                    "new_string": {"type": "string", "description": "Patch replacement text"},
                },
                "required": ["action", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_promote",
            "description": "Promote a runtime skill to a higher promotion level.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name"},
                    "category": {"type": "string", "description": "Optional skill category"},
                    "promotion_level": {"type": "string", "description": "Target promotion level"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_driver",
            "description": "Search the driver library for reusable hardware drivers. Use to find existing .h/.c driver files by chip, protocol, vendor, or keyword before writing new driver code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "Search keyword matching driver name, chip, vendor, device, protocol, or file path"},
                    "protocol": {"type": "string", "description": "Filter by communication protocol (e.g. I2C, SPI, UART, GPIO)"},
                    "vendor": {"type": "string", "description": "Filter by chip vendor (e.g. ST, TI, NXP)"},
                    "limit": {"type": "integer", "description": "Maximum results to return, default 20"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_execute",
            "description": "Execute an executable runtime skill and collect evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name"},
                    "category": {"type": "string", "description": "Optional skill category"},
                    "project": {"type": "string", "description": "Project name"},
                    "port": {"type": "string", "description": "Optional serial port"},
                    "baudrate": {"type": "integer", "description": "Optional monitor baudrate"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_read",
            "description": "Read durable memory or user memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "memory or user"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_write",
            "description": "Write durable memory or user memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Content to write"},
                    "target": {"type": "string", "description": "memory or user"},
                    "append": {"type": "boolean", "description": "Append when true; replace when false"},
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "Search memory, lessons, and recall context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lesson_list",
            "description": "List recorded lessons.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lesson_search",
            "description": "Search recorded lessons.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Maximum number of results"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lesson_record",
            "description": "Record a lesson candidate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "payload": {"type": "object", "description": "Lesson payload"},
                    "promoted": {"type": "boolean", "description": "Store directly as promoted"},
                },
                "required": ["payload"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lesson_promote",
            "description": "Promote a lesson into promoted state with evidence count.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "Lesson slug"},
                    "evidence_count": {"type": "integer", "description": "Evidence count"},
                },
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_inspect",
            "description": "Inspect the runtime workspace layout and roots.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_build",
            "description": "Build a project through the workspace runtime primitive. NOTE: stm32cubemx projects MUST have code generated by STM32CubeMX first — do NOT build a freshly-created cubemx project (it will fail).",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "clean": {"type": "boolean", "description": "Whether to clean first"},
                },
                "required": ["project"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_list_projects",
            "description": "List all existing projects in the workspace with their MCU, platform, and system info.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_create_project",
            "description": "Create a new project in the workspace with specified MCU, platform, and system. stm32cubemx creates only App/ and BSP/ — user must use STM32CubeMX to generate Core/Drivers/CMake/toolchain files afterwards. stm32firmware auto-creates a ready-to-build template. For cubemx: do NOT call workspace_build before CubeMX generates code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Project name"},
                    "mcu": {"type": "string", "description": "Target MCU (e.g. STM32F103C8)"},
                    "platform": {"type": "string", "description": "Platform: stm32cubemx or stm32firmware"},
                    "runtime": {"type": "string", "description": "System: baremetal or freertos"},
                    "firmware_package": {"type": "string", "description": "Firmware package name"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_read_file",
            "description": "Read a file from the current project. REQUIRED: project (the project name you are operating in) and path (relative to project root, e.g. Core/Src/main.c). Returns error if project or path is empty.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "path": {"type": "string", "description": "File path relative to project root"},
                },
                "required": ["project", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_flash",
            "description": "Flash a project through the workspace runtime primitive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "probe": {"type": "string", "description": "Optional probe"},
                },
                "required": ["project"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_monitor",
            "description": "Monitor a project through the workspace runtime primitive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "port": {"type": "string", "description": "Serial port"},
                    "baudrate": {"type": "integer", "description": "Baudrate"},
                },
                "required": ["project", "port"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_probe",
            "description": "Run a workspace probe primitive such as i2c.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "probe_type": {"type": "string", "description": "Probe type"},
                },
                "required": ["project"],
            },
        },
    },


    {
        "type": "function",
        "function": {
            "name": "workspace_write_file",
            "description": "Write content to a file within a project. Use this to create or overwrite any source file, header, CMakeLists.txt, or config file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "path": {"type": "string", "description": "Relative file path within the project directory (e.g., src/main.c, CMakeLists.txt)"},
                    "content": {"type": "string", "description": "Full file content to write"}
                },
                "required": ["project", "path", "content"]
            }
        }
    },


    {
        "type": "function",
        "function": {
            "name": "workspace_shell",
            "description": "Execute a read-only shell command in the project directory to inspect files. Use cat/type/head/tail to read files, rg/grep/findstr to search, ls/dir/find to list or locate files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "command": {"type": "string", "description": "Read-only shell command (cat, type, rg, grep, head, tail, wc, find, ls, dir, findstr)"}
                },
                "required": ["project", "command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_monitor_start",
            "description": "Start persistent background serial port monitoring. Output streams to frontend in real-time via SSE. Use before flashing to capture boot logs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "port": {"type": "string", "description": "Serial port, e.g. COM3"},
                    "baudrate": {"type": "integer", "description": "Baudrate, default 115200"}
                },
                "required": ["project", "port"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_monitor_stop",
            "description": "Stop persistent background serial port monitoring and release the port.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"}
                },
                "required": ["project"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_monitor_status",
            "description": "Get current state and recent output of the background serial monitor.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"}
                },
                "required": ["project"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_document_engineering",
            "description": "Parse attached documents (PDF, image, or text) and extract engineering facts: pin requirements, bus interfaces, protocol frames, register hints, bringup sequences, and timing constraints. Use when the user attaches datasheets, schematics, or manuals.",
            "parameters": {
                "type": "object",
                "properties": {
                    "docs": {"type": "array", "items": {"type": "string"}, "description": "List of document file paths to analyze"},
                    "query": {"type": "string", "description": "Optional query to focus analysis (e.g., chip name, protocol)"}
                },
                "required": ["docs"]
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "workspace_publish_driver",
            "description": "Publish a manually-written driver from a project to the shared driver library. Copies .h/.c files with content dedup and variant support. Use after writing and testing a driver via workspace_write_file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Source project name"},
                    "header_path": {"type": "string", "description": "Relative path to .h file within project (e.g., Drivers/ch1116/ch1116.h)"},
                    "source_path": {"type": "string", "description": "Relative path to .c file within project (e.g., Drivers/ch1116/ch1116.c)"},
                    "variant": {"type": "string", "description": "Optional variant name for same-chip different implementations (e.g., 128x64, 128x32)"},
                    "force": {"type": "boolean", "description": "Skip dedup check and force publish"}
                },
                "required": ["project", "header_path", "source_path"]
            }
        }
    },

]
PUBLIC_TOOL_NAMES = frozenset(tool["function"]["name"] for tool in TOOLS)
