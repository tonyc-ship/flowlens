#!/usr/bin/env python3
"""
选题评分脚本

根据笔记的互动数据评分选题质量
"""

import random
from time import sleep
from typing import List, Dict


def score_topic(notes: List[Dict]) -> float:
    """
    评分选题质量
    
    score = avg_like*0.5 + avg_collect*0.3 + avg_comment*0.2
    
    Args:
        notes: [
            {
                "liked_count": int,
                "collected_count": int,
                "comment_count": int
            },
            ...
        ]
    
    Returns:
        float: 0-100 评分
    """
    try:
        sleep(random.uniform(1, 2))
        
        if not notes:
            return 0.0
        
        n = len(notes)
        
        avg_like = sum(note.get("liked_count", 0) for note in notes) / n
        avg_collect = sum(note.get("collected_count", 0) for note in notes) / n
        avg_comment = sum(note.get("comment_count", 0) for note in notes) / n
        
        score = avg_like * 0.5 + avg_collect * 0.3 + avg_comment * 0.2
        
        return float(score)
    
    except Exception as e:
        print(f"[Error] score_topic 异常: {e}")
        return 0.0


if __name__ == "__main__":
    pass
