"""
Module for optimizing the order of hunks in a diff.
"""

from typing import List, Dict, Any, Set, Tuple

def optimize_hunk_order(hunks: List[Dict[str, Any]]) -> List[int]:
    """
    Optimize the order of hunks to minimize conflicts.
    
    Args:
        hunks: List of hunks to optimize
        
    Returns:
        List of indices representing the optimal order
    """
    # If there's only one hunk, no need to optimize
    if len(hunks) <= 1:
        return list(range(len(hunks)))
    
    # Sort hunks by old_start in reverse order to avoid position shifts
    # This way, we apply hunks from bottom to top
    hunk_indices = list(range(len(hunks)))
    hunk_indices.sort(key=lambda i: hunks[i]['old_start'], reverse=True)
    
    return hunk_indices

def group_related_hunks(hunks: List[Dict[str, Any]]) -> List[List[int]]:
    """
    Group hunks that are related and should be applied together.
    
    Args:
        hunks: List of hunks to group
        
    Returns:
        List of lists of indices representing groups of related hunks
    """
    # If there's only one hunk, no need to group
    if len(hunks) <= 1:
        return [[0]]
    
    # Build a graph of hunk relationships
    graph = {}
    for i in range(len(hunks)):
        graph[i] = set()
    
    # Two hunks are related if they overlap or are adjacent
    for i in range(len(hunks)):
        for j in range(i + 1, len(hunks)):
            hunk_i = hunks[i]
            hunk_j = hunks[j]
            
            # Check if hunks overlap or are adjacent
            i_start = hunk_i['old_start']
            i_end = i_start + len(hunk_i['old_block'])
            j_start = hunk_j['old_start']
            j_end = j_start + len(hunk_j['old_block'])
            
            # If hunks overlap or are adjacent (within 5 lines), they are related
            if (i_start <= j_end + 5 and j_start <= i_end + 5):
                graph[i].add(j)
                graph[j].add(i)
    
    # Find connected components in the graph
    visited = set()
    groups = []
    
    def dfs(node: int, component: List[int]):
        visited.add(node)
        component.append(node)
        for neighbor in graph[node]:
            if neighbor not in visited:
                dfs(neighbor, component)
    
    for i in range(len(hunks)):
        if i not in visited:
            component = []
            dfs(i, component)
            groups.append(component)
    
    return groups
