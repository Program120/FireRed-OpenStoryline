"""
Build the default pipeline DAG from node metadata.

Reads require_prior_kind from NODE_REGISTRY to construct edges automatically,
ensuring the DAG stays in sync with node definitions.
"""

from open_storyline.state.task_dag import TaskDAG, DAGEdge


def build_default_dag() -> TaskDAG:
    """
    Construct the default pipeline DAG from hardcoded node relationships.

    Uses node_kind as the DAG node identifier (matching require_prior_kind convention).
    This is called once at startup; the resulting DAG is immutable for the session.
    """
    # All node_kinds in the pipeline
    node_kinds = [
        "load_media",
        "search_media",
        "split_shots",
        "asr",
        "speech_rough_cut",
        "understand_clips",
        "filter_clips",
        "group_clips",
        "script_template_rec",
        "generate_script",
        "tts",
        "music_rec",
        "transition_rec",
        "text_rec",
        "plan_timeline",
        "render",
    ]

    # Edges derived from NodeMeta.require_prior_kind across all core nodes.
    # source -> target means "source must complete before target can start"
    edges = [
        # load_media is root (no deps)
        # search_media is root (no deps, can feed into load_media)
        DAGEdge(source="search_media", target="load_media"),

        # split_shots requires load_media
        DAGEdge(source="load_media", target="split_shots"),

        # asr requires split_shots
        DAGEdge(source="split_shots", target="asr"),

        # speech_rough_cut requires asr
        DAGEdge(source="asr", target="speech_rough_cut"),

        # understand_clips requires load_media, split_shots
        DAGEdge(source="load_media", target="understand_clips"),
        DAGEdge(source="split_shots", target="understand_clips"),

        # filter_clips requires split_shots, understand_clips
        DAGEdge(source="split_shots", target="filter_clips"),
        DAGEdge(source="understand_clips", target="filter_clips"),

        # group_clips requires filter_clips
        DAGEdge(source="filter_clips", target="group_clips"),

        # script_template_rec has no hard deps (can run independently)

        # generate_script requires split_shots, group_clips, understand_clips
        DAGEdge(source="split_shots", target="generate_script"),
        DAGEdge(source="group_clips", target="generate_script"),
        DAGEdge(source="understand_clips", target="generate_script"),

        # tts (generate_voiceover) requires group_clips, generate_script
        DAGEdge(source="group_clips", target="tts"),
        DAGEdge(source="generate_script", target="tts"),

        # music_rec (select_bgm) has no deps — independent branch

        # transition_rec requires group_clips
        DAGEdge(source="group_clips", target="transition_rec"),

        # text_rec requires generate_script
        DAGEdge(source="generate_script", target="text_rec"),

        # plan_timeline requires load_media, split_shots, group_clips, generate_script
        # also optionally uses tts, music_rec, transition_rec, text_rec
        DAGEdge(source="load_media", target="plan_timeline"),
        DAGEdge(source="split_shots", target="plan_timeline"),
        DAGEdge(source="group_clips", target="plan_timeline"),
        DAGEdge(source="generate_script", target="plan_timeline"),
        DAGEdge(source="tts", target="plan_timeline"),
        DAGEdge(source="music_rec", target="plan_timeline"),
        DAGEdge(source="transition_rec", target="plan_timeline"),
        DAGEdge(source="text_rec", target="plan_timeline"),

        # render requires plan_timeline (+ load_media for media files)
        DAGEdge(source="plan_timeline", target="render"),
        DAGEdge(source="load_media", target="render"),
    ]

    return TaskDAG(nodes=node_kinds, edges=edges)
