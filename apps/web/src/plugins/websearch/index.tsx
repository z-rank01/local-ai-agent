import type {CapabilityPanelProps} from '../../extensions/contracts';

export default function SearchSettings(_: CapabilityPanelProps) {
  return <p className="panel-hint">启用后模型可检索公开网页并读取正文；停用后可继续使用工作区文件和历史会话。开关由能力卡片统一管理。</p>;
}
