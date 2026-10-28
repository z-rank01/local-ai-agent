import {useCallback, useEffect, useRef, useState} from 'react';
import type {CSSProperties, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent} from 'react';
import {clamp} from '../lib/format';
import {SkillsPanel} from '../panels/SkillsPanel';
import {WorkspacePanel, type WorkspacePanelProps} from '../panels/WorkspacePanel';
import {WorkspaceManager} from '../panels/WorkspaceManager';
import type {ComponentProps} from 'react';
import {useLayerFocus} from '../ui/useLayerFocus';
import type {ConversationSummary} from '../types';
import {Icon} from '../ui/Icon';
import {Markdown} from '../ui/Markdown';
import {Panel} from '../ui/Panel';
import {RunsPanel} from '../panels/RunsPanel';
import {confirmCapabilityLeave} from '../lib/capabilityLeave';

export type SheetView = 'workbench' | 'doc' | 'runs';

export type SheetDoc = {
  title: string;
  content: string;
};

export type SheetProps = {
  open: boolean;
  onClose: () => void;
  view: SheetView;
  onViewChange: (view: SheetView) => void;
  width: number;
  onWidthChange: (width: number) => void;
  doc: SheetDoc | null;
  onZoomDoc: () => void;
  workspace: WorkspacePanelProps;
  workspaceManager: ComponentProps<typeof WorkspaceManager>;
  skills: {
    conversation?: ConversationSummary;
    onChanged?: () => void;
    onMethodChanged?: (conversation: ConversationSummary) => void;
  };
  runs: ComponentProps<typeof RunsPanel>;
};

const MIN_WIDTH = 340;
const MAX_WIDTH = 760;
const DEFAULT_WIDTH = 400;
const WIDTH_STEP = 16;

/* 右侧工作台：工作台视图（工作区 + 技能两个面板）与文档预览视图共用同一个 Sheet，
   左缘拖宽柄改的是 --sheet-w，宽度值由 App 持有并落库。
   两个滚动容器始终挂载、只用 hidden 切换，切视图不丢滚动位置。 */
export function Sheet({
  open,
  onClose,
  view,
  onViewChange,
  width,
  onWidthChange,
  doc,
  onZoomDoc,
  workspace, workspaceManager,
  skills,
  runs,
}: SheetProps): JSX.Element {
  const [overlay, setOverlay] = useState(window.innerWidth - width < 560);
  useEffect(() => { const update = () => setOverlay(window.innerWidth - width < 560); update(); window.addEventListener("resize", update); return () => window.removeEventListener("resize", update); }, [width]);
  const layerRef = useLayerFocus(open, overlay);
  const dragPointerRef = useRef<number | null>(null);

  const applyWidth = useCallback(
    (next: number) => {
      onWidthChange(Math.round(clamp(next, MIN_WIDTH, MAX_WIDTH)));
    },
    [onWidthChange],
  );

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerType === 'mouse' && event.button !== 0) {
      return;
    }
    event.preventDefault();
    dragPointerRef.current = event.pointerId;
    event.currentTarget.setPointerCapture(event.pointerId);
    event.currentTarget.focus({preventScroll: true});
  };

  // Sheet 贴右缘：宽度 = 视口宽度 - 指针横坐标。
  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (dragPointerRef.current !== event.pointerId) {
      return;
    }
    applyWidth(window.innerWidth - event.clientX);
  };

  const handlePointerEnd = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (dragPointerRef.current !== event.pointerId) {
      return;
    }
    dragPointerRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  // 贴右缘固定，所以左箭头变宽、右箭头变窄。
  const handleResizeKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') {
      return;
    }
    event.preventDefault();
    applyWidth(width + (event.key === 'ArrowLeft' ? WIDTH_STEP : -WIDTH_STEP));
  };

  const title = view === 'doc' && doc ? doc.title : view==='runs'?'全部任务':'工作台';

  return (
    <aside
      ref={layerRef}
      className={open ? 'sheet open' : 'sheet'}
      style={{'--sheet-w': `${width}px`} as CSSProperties}
      role="dialog"
      aria-modal={overlay}
      aria-label="工作台"
    >
      <div
        className="sheet-resize"
        role="separator"
        aria-orientation="vertical"
        aria-label="调整侧栏宽度"
        aria-valuemin={MIN_WIDTH}
        aria-valuemax={MAX_WIDTH}
        aria-valuenow={Math.round(clamp(width, MIN_WIDTH, MAX_WIDTH))}
        tabIndex={0}
        title="拖动调整宽度 · 双击复原"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerEnd}
        onPointerCancel={handlePointerEnd}
        onDoubleClick={() => applyWidth(DEFAULT_WIDTH)}
        onKeyDown={handleResizeKeyDown}
      />
      <div className="sheet-head">
        {view !== 'workbench' ? (
          <button type="button" className="sheet-back" onClick={() => onViewChange('workbench')}>
            <Icon name="i-chev-left" className="ico-sm" />
            工作台
          </button>
        ) : null}
        <span className="sheet-title">{title}</span>
        {view==='workbench'&&<button className="btn btn-quiet btn-sm" onClick={()=>onViewChange('runs')}>查看全部任务</button>}
        <span id="sheetActs">
          {view === 'doc' ? (
            <button type="button" className="btn btn-quiet btn-sm" onClick={onZoomDoc}>
              <Icon name="i-expand" className="ico-sm" />
              放大
            </button>
          ) : null}
        </span>
        <button type="button" className="tb-btn" aria-label="关闭工作台" onClick={onClose}>
          <Icon name="i-x" />
        </button>
      </div>
      <div className="sheet-scroll" hidden={view!=='runs'}><RunsPanel {...runs}/></div>
      <div className="sheet-scroll" hidden={view !== 'workbench'}>
        <Panel id="workspace" title="工作区">
          <WorkspaceManager {...workspaceManager} />
          <WorkspacePanel key={workspace.workspaceId} {...workspace} />
        </Panel>
        <Panel id="skills" title="能力" beforeToggle={confirmCapabilityLeave}>
          <SkillsPanel workspaceId={workspace.workspaceId} {...skills} />
        </Panel>
      </div>
      <div className="sheet-doc" hidden={view !== 'doc'}>
        {doc ? (
          <div className="doc-view">
            <Markdown content={doc.content} />
          </div>
        ) : null}
      </div>
    </aside>
  );
}
