from typing import Dict, Any
import yaml

class TopicPageRenderer:
    def render(self, payload: Dict[str, Any]) -> str:
        frontmatter = {
            "type": "topic_page",
            "topic_id": payload["topic_id"],
            "status": payload["status"],
        }
        
        path_str = " > ".join(payload["topic_path"])
        
        summary = payload.get("summary_content") or "暂无概览信息"
        
        subtopics_md = ""
        if payload.get("subtopics"):
            subtopics_md = "\n### 子主题\n" + "\n".join(f"- [[{st['name']}]] - {st.get('description', '')}" for st in payload["subtopics"])
            
        notes_md = "暂无相关知识笔记"
        if payload.get("knowledge_notes"):
            notes_md = "\n".join(f"- [[{n['path']}|{n['title']}]]" for n in payload["knowledge_notes"])
        
        return f"""---
{yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False).strip()}
---

# {payload["topic_path"][-1]}
**路径**: {path_str}

## 概览
{summary}
{subtopics_md}

## 详情积累
{notes_md}
"""
