/**
 * 递归计算树中父节点下某类子节点的总数（包含嵌套子节点）。
 *
 * @param {Array} nodes - 树节点数组
 * @param {string} parentType - 父节点类型标识，如 'collection' / 'page'
 * @param {string} childType - 需要统计的子节点类型标识，如 'request' / 'element'
 * @param {string} countKey - 写入父节点的数量字段名，默认 'count'
 */
export const computeTreeChildCount = (nodes, parentType, childType, countKey = 'count') => {
  nodes.forEach(node => {
    if (node.children) {
      computeTreeChildCount(
        node.children.filter(child => child.type === parentType),
        parentType,
        childType,
        countKey
      )
    }
    const directCount = (node.children || []).filter(child => child.type === childType).length
    const nestedCount = (node.children || [])
      .filter(child => child.type === parentType)
      .reduce((sum, child) => sum + (child[countKey] || 0), 0)
    node[countKey] = directCount + nestedCount
  })
}
