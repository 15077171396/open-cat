import type { ReactNode } from 'react'
import {
  ApartmentOutlined,
  AreaChartOutlined,
  AuditOutlined,
  BookOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CodeOutlined,
  MedicineBoxOutlined,
  CommentOutlined,
  ControlOutlined,
  CreditCardOutlined,
  CloudServerOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  DesktopOutlined,
  DeleteOutlined,
  FileSearchOutlined,
  FileProtectOutlined,
  FileTextOutlined,
  FundProjectionScreenOutlined,
  GiftOutlined,
  HomeOutlined,
  IdcardOutlined,
  LoginOutlined,
  MailOutlined,
  MenuOutlined,
  MessageOutlined,
  NotificationOutlined,
  PictureOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  SettingOutlined,
  SolutionOutlined,
  SoundOutlined,
  TagsOutlined,
  TeamOutlined,
  UserOutlined,
  WifiOutlined,
  WalletOutlined,
} from '@ant-design/icons'

/**
 * 菜单图标名 → 图标组件。
 *
 * 后端 `app/services/admin_menu.py` 只下发**图标名字符串**（React 组件不可 JSON 序列化），
 * 由本表解析成节点。新增图标时两处都要改 ——
 * `backend/tests/test_admin_menu.py` 会比对后端用到的图标名是否都在本表里，
 * 漏加不会报错，只会让菜单静默丢图标。
 */
const MENU_ICON_MAP: Record<string, ReactNode> = {
  // 分组图标
  home: <HomeOutlined />,
  'file-text': <FileTextOutlined />,
  notification: <NotificationOutlined />,
  idcard: <IdcardOutlined />,
  control: <ControlOutlined />,

  // 叶子图标
  dashboard: <DashboardOutlined />,
  workplace: <DesktopOutlined />,
  chart: <AreaChartOutlined />,
  code: <CodeOutlined />,
  dict: <BookOutlined />,
  monitor: <FundProjectionScreenOutlined />,
  online: <WifiOutlined />,
  server: <CloudServerOutlined />,
  cache: <DatabaseOutlined />,
  position: <SolutionOutlined />,
  picture: <PictureOutlined />,
  book: <BookOutlined />,
  tags: <TagsOutlined />,
  sound: <SoundOutlined />,
  audit: <AuditOutlined />,
  message: <MessageOutlined />,
  comment: <CommentOutlined />,
  search: <SearchOutlined />,
  team: <TeamOutlined />,
  'check-circle': <CheckCircleOutlined />,
  wallet: <WalletOutlined />,
  gift: <GiftOutlined />,
  safety: <SafetyCertificateOutlined />,
  apartment: <ApartmentOutlined />,
  robot: <RobotOutlined />,
  setting: <SettingOutlined />,
  menu: <MenuOutlined />,
  login: <LoginOutlined />,
  'file-search': <FileSearchOutlined />,
  'file-protect': <FileProtectOutlined />,
  'credit-card': <CreditCardOutlined />,
  mail: <MailOutlined />,
  clock: <ClockCircleOutlined />,
  readiness: <MedicineBoxOutlined />,
  delete: <DeleteOutlined />,
  database: <DatabaseOutlined />,
  user: <UserOutlined />,
}

/** 按名字取图标；未知名字返回 undefined（菜单项退化为纯文字，不报错不白屏） */
export function menuIcon(name?: string): ReactNode | undefined {
  return name ? MENU_ICON_MAP[name] : undefined
}

/** 菜单管理页图标下拉的可选项（与渲染映射同源，只暴露已注册的名字） */
export const MENU_ICON_NAMES = Object.keys(MENU_ICON_MAP)
