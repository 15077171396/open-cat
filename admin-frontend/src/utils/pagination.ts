/** 删除若干条后判断是否应回退一页（当前页将删空且不在第一页）；单删与批量删除共用 */
export function shouldRetreatOnePage(remaining: number, page: number, pageSize: number): boolean {
  return page > 1 && remaining <= (page - 1) * pageSize
}
