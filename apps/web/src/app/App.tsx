import {BriefReaderContext} from '../lib/briefReader';
import {prepareHtmlBrief} from '../lib/htmlBrief';
import {useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {
  DEFAULT_BASE_URL, fetchRuns, fetchRun, controlRun, observeRun,
  fetchWorkspacePreview,
  deliverWorkspaceBrief,
  ensureBrowserSession,
  ApiError,
  fetchWorkspaces,
  activateMessageVersion,
  deleteConversation,
  deleteMessage,
  downloadConversation,
  fetchConversations,
  fetchConversation,
  fetchMessages,
  fetchModels,
  fetchProviders,
  fetchStatus,
  shutdownStack,
  streamChat,
  streamEditMessage,
  streamRegenerate,
  updateConversationTitle,
  uploadWorkspaceFile,
} from '../api';
import {
  DEFAULT_APPEARANCE,
  densityScale,
  loadAppearanceSettings,
  normalizeAppearanceSettings,
  type AppearanceSettings,
} from '../lib/appearance';
import {askConfirm, askForm, usePendingDialog} from '../lib/confirm';
import {clamp, copyText, eventText, formatBackendError} from '../lib/format';
import {notify} from '../lib/notify';
import {watchTheme} from '../lib/theme';
import {readConversationSummary, upsertConversation, type ReadingReport} from '../lib/report';
import {STORAGE_KEYS, readJson, readString, writeJson, writeString} from '../lib/storage';
import {
  appendOrUpdate,
  buildBlocksFromMessages,
  buildTranscriptViewItems,
  insertBeforeBlock,
  type EditingState,
} from '../lib/transcript';
import type {
  AppStatus, RunList, RunSummary,
  ConversationSummary,
  Workspace,
  ModelInfo,
  ProviderInfo,
  TranscriptBlock,
  UIStreamEvent,
} from '../types';
import {ConfirmDialog} from '../ui/ConfirmDialog';
import {Icon, IconSprite} from '../ui/Icon';
import {Toast} from '../ui/Toast';
import {WorkspaceContext, ToolNamesContext} from '../lib/workspace';
import {Composer} from './Composer';
import {RunControls} from '../panels/RunsPanel';
import {RUN_LIVE,conversationRun,acceptRunEvent,runLabel} from '../lib/runs';
import {ConversationDrawer} from './ConversationDrawer';
import {Reader} from './Reader';
import {SettingsDialog, type SettingsTab} from './SettingsDialog';
import {Sheet, type SheetView} from './Sheet';
import {TopBar, type ExportFormat} from './TopBar';
import {Transcript} from './Transcript';

const DELTA_FLUSH_MS = 48;
const TOOL_TEXT_LIMIT = 102400;
const TOOL_TEXT_TRUNCATION_NOTICE = '（内容过长，仅显示最近 100KB）';
const SHEET_MIN_WIDTH = 340;
const SHEET_MAX_WIDTH = 760;
const SHEET_DEFAULT_WIDTH = 400;
const STICKY_BOTTOM_THRESHOLD = 80;
const TOPBAR_SCROLLED_OFFSET = 4;
const CONVERSATION_REFRESH_DEBOUNCE_MS = 180;
const APPEARANCE_CSS_VARS = ['--ui-font', '--ui-size', '--ui-leading', '--code-font', '--code-size', '--read-width'] as const;
const DENSITY_CSS_VARS = ['--space-2', '--space-3', '--space-4', '--space-6'] as const;
const DENSITY_BASE_PX: Record<(typeof DENSITY_CSS_VARS)[number], number> = {
  '--space-2': 8,
  '--space-3': 12,
  '--space-4': 16,
  '--space-6': 24,
};

export default function App(): JSX.Element {
  const [status, setStatus] = useState<AppStatus | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [selectedModelId, setSelectedModelId] = useState<string>('');
  const [modelsRefreshing, setModelsRefreshing] = useState(false);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState(readString('local-ai-agent.workspace') ?? 'default');
  const [currentSummary, setCurrentSummary] = useState<ConversationSummary>();
  const [conflictConversationId, setConflictConversationId] = useState<string>();
  const [workspaceFilesBusy, setWorkspaceFilesBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [blocks, setBlocks] = useState<TranscriptBlock[]>([]);
  const [input, setInput] = useState('');
  const [editText, setEditText] = useState('');
  const [attachedPaths, setAttachedPaths] = useState<string[]>([]);
  const [editingMessage, setEditingMessage] = useState<EditingState | null>(null);
  const [conversationFilter, setConversationFilter] = useState('');
  /* 沉浸会话：会话列表与工作台都是按需唤出的覆盖层，默认一律收起 */
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [sheetView, setSheetView] = useState<SheetView>('workbench');
  const [sheetWidth, setSheetWidth] = useState(() => {
    const stored = Number.parseInt(readString(STORAGE_KEYS.sheetWidth) ?? '', 10);
    return Number.isFinite(stored) ? clamp(stored, SHEET_MIN_WIDTH, SHEET_MAX_WIDTH) : SHEET_DEFAULT_WIDTH;
  });
  const [sheetDoc, setSheetDoc] = useState<ReadingReport | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>('appearance');
  const [readingReport, setReadingReport] = useState<ReadingReport | null>(null);
  const [readerZoomed, setReaderZoomed] = useState(false);
  const [appearance, setAppearance] = useState<AppearanceSettings>(() => loadAppearanceSettings());
  const [busy, setBusy] = useState(false);
  const [runList,setRunList]=useState<RunList>({runs:[],stock_service:{online:false}});
  const [runError,setRunError]=useState('');
  const [observation,setObservation]=useState('');
  const runCursors=useRef<Record<string,number>>({});
  const currentRun=conversationRun(runList.runs,conversationId);
  const refreshRuns=useCallback(async()=>{
    const next=await fetchRuns(); setRunList(next); setRunError(''); return next;
  },[]);
  const [versionBusy, setVersionBusy] = useState(false);
  const [conversationLoading, setConversationLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [backendOnline, setBackendOnline] = useState(false);
  const [shutdownPending, setShutdownPending] = useState(false);
  const [stackStopped, setStackStopped] = useState(false);
  const pendingDialog = usePendingDialog();

  const workspaceIdRef = useRef(workspaceId);
  workspaceIdRef.current = workspaceId;
  const streamFailureRef = useRef<string | null>(null);
  const streamCompletedRef = useRef(false);
  const runIdentityRef = useRef<{conversationId: string | null; runId?: string} | null>(null);
  const loadSequenceRef = useRef(0);
  const searchSequenceRef = useRef(0);
  const draftHydratedRef = useRef(false);
  const draftRef = useRef({input, attachedPaths, conversationId, workspaceId});
  draftRef.current = {input, attachedPaths, conversationId, workspaceId};
  const readerReturnToSheetRef = useRef(false);
  const reportTriggerRef = useRef<HTMLElement | null>(null);
  const reportReadingPositionsRef = useRef(new Map<string, ReadingReport>());
  const deltaBufferRef = useRef<Record<string, {kind: 'assistant' | 'reasoning'; text: string}>>({});
  const deltaTimerRef = useRef<number | null>(null);
  const pendingPromptRef = useRef('');
  const pendingUserBlockIdRef = useRef<string | null>(null);
  const pendingAssistantBlockIdRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const stickToBottomRef = useRef(true);
  const suppressNextAutoScrollRef = useRef(false);
  const activeConversationIdRef = useRef<string | null>(readString(STORAGE_KEYS.activeConversation));

  const effectiveAppearance = useMemo(() => normalizeAppearanceSettings(appearance), [appearance]);
  const selectedModel = useMemo(
    () => models.find((model) => model.id === selectedModelId) ?? models.find((model) => model.default) ?? models[0],
    [models, selectedModelId],
  );
  const transcriptItems = useMemo(() => buildTranscriptViewItems(blocks), [blocks]);
  const activeConversation = useMemo(
    () => conversations.find((item) => item.id === conversationId) ?? currentSummary,
    [conversationId, conversations, currentSummary],
  );

  const handleCopy = useCallback(async (text: string) => {
    const ok = await copyText(text);
    notify(ok ? '已复制到剪贴板' : '复制失败', ok ? 'ok' : 'error');
  }, []);

  const updateAppearance = useCallback((patch: Partial<AppearanceSettings>) => {
    setAppearance((current) => normalizeAppearanceSettings({...current, ...patch}));
  }, []);

  const resetAppearance = useCallback(() => {
    setAppearance(DEFAULT_APPEARANCE);
    notify('外观已重置');
  }, []);

  const flushDeltaBuffer = useCallback(() => {
    const buffered = Object.entries(deltaBufferRef.current);
    if (!buffered.length) {
      return;
    }
    deltaBufferRef.current = {};
    setBlocks((current) => {
      let next = current;
      for (const [id, item] of buffered) {
        next = appendOrUpdate(
          next,
          id,
          {
            id,
            kind: item.kind,
            label: item.kind === 'assistant' ? '助手' : '思考过程',
            text: '',
            status: 'running',
            collapsible: item.kind === 'reasoning',
            collapsed: false,
          },
          (block) => ({...block, text: `${block.text}${item.text}`, status: 'running', placeholder: false}),
        );
      }
      return next;
    });
  }, []);

  const queueDelta = useCallback((kind: 'assistant' | 'reasoning', blockId: string, text: string) => {
    const current = deltaBufferRef.current[blockId];
    deltaBufferRef.current[blockId] = current
      ? {...current, text: `${current.text}${text}`}
      : {kind, text};
    if (deltaTimerRef.current) {
      return;
    }
    deltaTimerRef.current = window.setTimeout(() => {
      deltaTimerRef.current = null;
      flushDeltaBuffer();
    }, DELTA_FLUSH_MS);
  }, [flushDeltaBuffer]);

  const closeReportReader = useCallback((restore = true) => {
    setReadingReport(null);
    setReaderZoomed(false);
    if (restore && readerReturnToSheetRef.current) { setSheetOpen(true); setSheetView("doc"); }
    readerReturnToSheetRef.current = false;
    if (restore && reportTriggerRef.current) {
      const trigger = reportTriggerRef.current;
      window.requestAnimationFrame(() => {
        if (trigger.isConnected) {
          trigger.focus({preventScroll: true});
          trigger.closest('.report-card')?.scrollIntoView({block: 'center'});
        }
      });
    }
  }, []);

  const draftKey = (id: string | null, ws: string) => `local-ai-agent.draft.${id ?? 'new.' + ws}`;
  const persistDraft = useCallback(() => {
    if (!draftHydratedRef.current) return;
    const draft = draftRef.current;
    writeJson(draftKey(draft.conversationId, draft.workspaceId), {input: draft.input, attachedPaths: draft.attachedPaths});
  }, []);
  const restoreDraft = useCallback((id: string | null, ws: string) => {
    const draft = readJson<{input: string; attachedPaths: string[]}>(draftKey(id, ws), {input: '', attachedPaths: []});
    draftHydratedRef.current = true;
    setInput(typeof draft.input === 'string' ? draft.input : '');
    setAttachedPaths(Array.isArray(draft.attachedPaths) ? draft.attachedPaths.filter(p => typeof p === 'string') : []);
  }, []);
  const refreshWorkspaces = useCallback(async () => { setWorkspaces(await fetchWorkspaces()); }, []);
  const refreshConversations = useCallback(async (_preferredId?: string | null, query?: string) => {
    const sequence = ++searchSequenceRef.current;
    const next = await fetchConversations(query, undefined, query?.trim() ? undefined : workspaceIdRef.current);
    if (sequence === searchSequenceRef.current) setConversations(next);
    return next;
  }, []);

  const detachObserver=useCallback(()=>{
    const previous=abortRef.current;
    abortRef.current=null; runIdentityRef.current=null;
    previous?.abort(); flushDeltaBuffer();
    pendingPromptRef.current='';pendingUserBlockIdRef.current=null;pendingAssistantBlockIdRef.current=null;
    setBusy(false);setObservation('');
  },[flushDeltaBuffer]);

  const openConversation = useCallback(async (targetId: string) => {
    detachObserver();
    persistDraft();
    const sequence = ++loadSequenceRef.current;
    flushDeltaBuffer();
    closeReportReader(false);
    setConversationLoading(true);
    try {
      const snapshot=await refreshRuns();
      const running=conversationRun(snapshot.runs,targetId);
      if(running) runCursors.current[running.id]=running.last_seq;
      const [messages, summary] = await Promise.all([fetchMessages(targetId), fetchConversation(targetId)]);
      if (!summary || sequence !== loadSequenceRef.current) return;
      const nextWorkspaceId = summary.workspace_id ?? 'default';
      setCurrentSummary(summary);
      setWorkspaceId(nextWorkspaceId);
      workspaceIdRef.current = nextWorkspaceId;
      setConversationId(targetId);
      activeConversationIdRef.current = targetId;
      restoreDraft(targetId, nextWorkspaceId);
      setBlocks(buildBlocksFromMessages(messages));
      setSheetDoc(null); setSheetView('workbench');
      if (summary.model) setSelectedModelId(summary.model);
      setEditingMessage(null); setEditText(''); setError(''); setConflictConversationId(undefined);
      stickToBottomRef.current = true;
      void fetchStatus(undefined, nextWorkspaceId).then(setStatus).catch(() => {});
    } catch (err) {
      if (sequence === loadSequenceRef.current) setError(formatBackendError(err));
    } finally {
      if (sequence === loadSequenceRef.current) setConversationLoading(false);
    }
  }, [closeReportReader, detachObserver, flushDeltaBuffer, persistDraft, restoreDraft, refreshRuns]);

  const refreshModels = useCallback(async () => {
    setModelsRefreshing(true);
    try {
      const [nextModels, nextProviders] = await Promise.all([fetchModels(true), fetchProviders(true)]);
      setModels(nextModels);
      setProviders(nextProviders);
    } finally {
      setModelsRefreshing(false);
    }
  }, []);

  const loadBootstrap = useCallback(async () => {
    setLoading(true);
    try {
      const [nextStatus, nextModels, nextProviders, nextConversations, nextWorkspaces] = await Promise.all([
        fetchStatus(undefined, workspaceIdRef.current),
        fetchModels(),
        fetchProviders(),
        fetchConversations(),
        fetchWorkspaces(),
      ]);
      setWorkspaces(nextWorkspaces);
      setStatus(nextStatus);
      setModels(nextModels);
      setProviders(nextProviders);
      setSelectedModelId((current) => (
        current && nextModels.some((model) => model.id === current)
          ? current
          : nextModels.find((model) => model.default)?.id ?? nextModels[0]?.id ?? ''
      ));
      setConversations(nextConversations);
      setBackendOnline(true);
      setError('');
      const activeId = activeConversationIdRef.current;
      const target = activeId && nextConversations.some((item) => item.id === activeId)
        ? activeId
        : nextConversations[0]?.id;
      if (target) {
        await openConversation(target);
      } else { restoreDraft(null, workspaceIdRef.current); }
    } catch (err) {
      if (!draftHydratedRef.current) restoreDraft(null, workspaceIdRef.current);
      setBackendOnline(false);
      setStatus(null);
      setModels([]);
      setProviders([]);
      setError(formatBackendError(err));
    } finally {
      setLoading(false);
    }
  }, [openConversation, restoreDraft]);

  const resetConversation = useCallback(() => {
    if (uploading || workspaceFilesBusy) return;
    detachObserver();
    persistDraft();
    ++loadSequenceRef.current;
    activeConversationIdRef.current = null;
    setConversationLoading(false);
    flushDeltaBuffer();
    pendingUserBlockIdRef.current = null;
    pendingAssistantBlockIdRef.current = null;
    setConversationId(null);
    setBlocks([]);
    restoreDraft(null, workspaceIdRef.current);
    setCurrentSummary(undefined);
    setEditText('');
    setEditingMessage(null);
    setError('');
    stickToBottomRef.current = true;
    closeReportReader(false);
  }, [detachObserver, closeReportReader, flushDeltaBuffer, persistDraft, restoreDraft, uploading, workspaceFilesBusy]);

  const attachWorkspacePath = useCallback((path: string) => {
    if (workspaceIdRef.current !== workspaceId || activeConversationIdRef.current !== conversationId) {
      const key = draftKey(conversationId, workspaceId);
      const cached = readJson<{input: string; attachedPaths: string[]}>(key, {input: '', attachedPaths: []});
      writeJson(key, {...cached, attachedPaths: [...new Set([...(Array.isArray(cached.attachedPaths) ? cached.attachedPaths : []), path])]});
      return;
    }
    setAttachedPaths((current) => (current.includes(path) ? current : [...current, path]));
  }, [workspaceId, conversationId]);

  const detachWorkspacePath = useCallback((path: string) => {
    setAttachedPaths((current) => current.filter((item) => item !== path));
  }, []);

  const beginEditMessage = useCallback((messageId: string, text: string) => {
    setEditingMessage({messageId, originalText: text});
    setEditText(text);
    setError('');
    notify('已进入编辑模式');
  }, []);

  const cancelEditMessage = useCallback(() => {
    setEditingMessage(null);
    setEditText('');
  }, []);

  const toggleBlock = (id: string) => {
    suppressNextAutoScrollRef.current = true;
    setBlocks((current) => current.map((block) => (
      block.id === id ? {...block, collapsed: !block.collapsed} : block
    )));
  };

  const applyEvent = useCallback((event: UIStreamEvent) => {
    const identity = runIdentityRef.current;
    if (!identity) return;
    if (identity.runId && event.run_id && identity.runId !== event.run_id) return;
    if (identity.conversationId && event.conversation_id && identity.conversationId !== event.conversation_id) return;
    if (event.run_id) {
      identity.runId = event.run_id;
      if(!acceptRunEvent(runCursors.current,event)) return;
    }
    if (event.conversation_id) { identity.conversationId = event.conversation_id; activeConversationIdRef.current = event.conversation_id; }
    if (event.event !== 'assistant.delta' && event.event !== 'reasoning.delta') {
      flushDeltaBuffer();
    }
    if (event.conversation_id) {
      setConversationId(event.conversation_id);
    }
    switch (event.event) {
      case 'run.updated': {
        const row=event.data.run as RunSummary;
        setRunList(list=>({...list,runs:[row,...list.runs.filter(r=>r.id!==row.id)]}));
        break;
      }
      case 'session.started': {
        const summary = readConversationSummary(event);
        if (summary) {
          setCurrentSummary(summary);
          setConversations((current) => upsertConversation(current, summary));
        }
        break;
      }
      case 'model.selected':
        setSelectedModelId(eventText(event, 'model'));
        break;
      case 'attachments.imported': {
        const attachments = event.data.attachments;
        const names = Array.isArray(attachments)
          ? attachments.map((item) => {
              if (typeof item === 'object' && item && 'display_name' in item) {
                return String((item as {display_name: unknown}).display_name);
              }
              return '';
            }).filter(Boolean).join(', ')
          : '';
        setBlocks((current) => insertBeforeBlock(current, pendingAssistantBlockIdRef.current, {
          id: `attach-${event.run_id ?? Date.now()}`,
          kind: 'meta',
          label: '附件',
          text: names ? `已导入：${names}` : '已导入附件',
        }));
        break;
      }
      case 'user.accepted': {
        const acceptedBlock: TranscriptBlock = {
          id: event.message_id ?? `user-${Date.now()}`,
          kind: 'user',
          label: '你',
          text: eventText(event, 'content') || pendingPromptRef.current,
          messageId: event.message_id ?? null,
        };
        const pendingUserId = pendingUserBlockIdRef.current;
        setBlocks((current) => {
          if (pendingUserId && current.some((block) => block.id === pendingUserId)) {
            return current.map((block) => block.id === pendingUserId ? {...block, ...acceptedBlock} : block);
          }
          if (current.some((block) => block.id === acceptedBlock.id)) {
            return current.map((block) => block.id === acceptedBlock.id ? {...block, ...acceptedBlock} : block);
          }
          return insertBeforeBlock(current, pendingAssistantBlockIdRef.current, acceptedBlock);
        });
        pendingUserBlockIdRef.current = null;
        pendingPromptRef.current = '';
        break;
      }
      case 'assistant.delta':
        if (pendingAssistantBlockIdRef.current) {
          const pendingId = pendingAssistantBlockIdRef.current;
          setBlocks((current) => current.filter((block) => block.id !== pendingId));
          pendingAssistantBlockIdRef.current = null;
        }
        queueDelta('assistant', event.block_id ?? `assistant-${Date.now()}`, eventText(event, 'text'));
        break;
      case 'reasoning.started':
        setBlocks((current) => [...current, {
          id: event.block_id ?? `reasoning-${Date.now()}`,
          kind: 'reasoning',
          label: '思考过程',
          text: '',
          status: 'running',
          collapsible: true,
          collapsed: false,
        }]);
        break;
      case 'reasoning.delta':
        queueDelta('reasoning', event.block_id ?? `reasoning-${Date.now()}`, eventText(event, 'text'));
        break;
      case 'reasoning.completed':
        if (event.block_id) {
          setBlocks((current) => appendOrUpdate(
            current,
            event.block_id as string,
            {
              id: event.block_id as string,
              kind: 'reasoning',
              label: '思考过程',
              text: '',
              status: 'ok',
              collapsible: true,
              collapsed: true,
            },
            (block) => ({...block, status: 'ok', collapsed: true}),
          ));
        }
        break;
      case 'tool.started':
        setBlocks((current) => [...current, {
          id: event.block_id ?? `tool-${Date.now()}`,
          kind: 'tool',
          label: typeof event.data.name === 'string' ? event.data.name : 'tool',
          text: eventText(event, 'summary'),
          summary: eventText(event, 'summary'),
          status: 'running',
          collapsible: true,
          collapsed: false,
          params: typeof event.data.params === 'object' && event.data.params ? event.data.params as Record<string, unknown> : undefined,
        }]);
        break;
      case 'tool.progress': {
        const detail = eventText(event, 'text');
        setBlocks((current) => current.map((block) => {
          if (block.id !== event.block_id) {
            return block;
          }
          const elapsed = typeof event.data.elapsed === 'number' ? event.data.elapsed : block.elapsed;
          if (event.data.event === 'heartbeat') {
            return {...block, summary: `${detail}（已等待 ${event.data.elapsed ?? 0} 秒）`, elapsed};
          }
          if (!detail) {
            return {...block, elapsed};
          }
          const combined = block.text + detail;
          const text = combined.length > TOOL_TEXT_LIMIT
            ? `${TOOL_TEXT_TRUNCATION_NOTICE}\n${combined.slice(-TOOL_TEXT_LIMIT)}`
            : combined;
          return {...block, text, elapsed};
        }));
        break;
      }
      case 'tool.completed': {
        const blockId = event.block_id ?? `tool-${Date.now()}`;
        const detail = eventText(event, 'detail');
        const statusValue = event.data.status === 'ok' ? 'ok' : 'error';
        const toolResult = Object.prototype.hasOwnProperty.call(event.data, 'result') ? event.data.result : undefined;
        setBlocks((current) => appendOrUpdate(
          current,
          blockId,
          {
            id: blockId,
            kind: 'tool',
            label: typeof event.data.name === 'string' ? event.data.name : 'tool',
            text: detail,
            summary: eventText(event, 'summary'),
            toolResult,
            messageId: event.message_id,
            status: statusValue,
            collapsible: true,
            collapsed: true,
            elapsed: typeof event.data.elapsed === 'number' ? event.data.elapsed : undefined,
          },
          (block) => ({
            ...block,
            text: detail || block.text,
            summary: eventText(event, 'summary') || eventText(event, 'headline') || (event.data.status === 'error' ? '执行失败' : '已完成'),
            toolResult: toolResult ?? block.toolResult,
            messageId: event.message_id ?? block.messageId,
            status: statusValue,
            elapsed: typeof event.data.elapsed === 'number' ? event.data.elapsed : block.elapsed,
            collapsed: (detail || block.text).length > 120 || (detail || block.text).includes('\n'),
          }),
        ));
        break;
      }
      case 'tool.local_result': {
        const localResult = eventText(event, 'local_result');
        if (!localResult) {
          break;
        }
        setBlocks((current) => current.map((block) => {
          if (block.id !== event.block_id) {
            return block;
          }
          const existing = (typeof block.toolResult === 'object' && block.toolResult !== null ? block.toolResult : {}) as Record<string, unknown>;
          return {...block, messageId: event.message_id ?? block.messageId,
            toolResult: {...existing, local_result: localResult}};
        }));
        break;
      }
      case 'assistant.completed': {
        const blockId = event.block_id;
        const text = eventText(event, 'text');
        const pendingId = pendingAssistantBlockIdRef.current;
        setBlocks((current) => {
          let next = pendingId ? current.filter((block) => block.id !== pendingId || block.text) : current;
          if (blockId && (text || next.some((block) => block.id === blockId))) {
            next = appendOrUpdate(
              next,
              blockId,
              {
                id: blockId,
                kind: 'assistant',
                label: '助手',
                text,
                status: 'ok',
              },
              (block) => ({...block, text: text || block.text, label: eventText(event, 'model') || block.label, messageId: event.message_id, status: event.data.status === 'error' ? 'error' : 'ok', placeholder: false}),
            );
          }
          return next;
        });
        pendingAssistantBlockIdRef.current = null;
        break;
      }
      case 'conversation.updated': {
        const summary = readConversationSummary(event);
        if (summary) {
          setCurrentSummary(summary);
          setConversations((current) => upsertConversation(current, summary));
        }
        break;
      }
      case 'session.completed':
        streamCompletedRef.current = true;
        void fetchStatus(undefined, workspaceIdRef.current).then(setStatus).catch(() => {});
        break;
      case 'error':
        streamFailureRef.current = eventText(event, 'message') || '请求失败';
        setError(streamFailureRef.current);
        break;
      default:
        break;
    }
  }, [conversationFilter, conversationId, flushDeltaBuffer, queueDelta, refreshConversations]);

  useEffect(()=>{
    let mounted=true;
    const poll=()=>void refreshRuns().catch(cause=>{if(mounted) setRunError(formatBackendError(cause));});
    poll(); const timer=window.setInterval(poll,3000);
    return ()=>{mounted=false;window.clearInterval(timer);};
  },[refreshRuns]);

  useEffect(()=>{
    const run=conversationRun(runList.runs,conversationId);
    if(!run||!RUN_LIVE.has(run.status)||abortRef.current||conversationLoading) return;
    const controller=new AbortController(); abortRef.current=controller;
    runIdentityRef.current={conversationId:run.conversation_id,runId:run.id};
    setBusy(true);setObservation(run.status==='PAUSED'?'':'正在连接运行记录…');
    void (async()=>{
      try {
        await observeRun(run.id,runCursors.current[run.id]??0,event=>{
          if(abortRef.current!==controller) return;
          setObservation('');applyEvent(event);
        },controller.signal);
        const final=await fetchRun(run.id);
        if(abortRef.current!==controller) return;
        setRunList(list=>({...list,runs:[final,...list.runs.filter(r=>r.id!==final.id)]}));
        if(RUN_LIVE.has(final.status)) setObservation('连接中断，正在重新连接；任务仍由后端持有');
        else {
          const messages=await fetchMessages(run.conversation_id!);
          if(abortRef.current===controller) {flushDeltaBuffer();setBlocks(buildBlocksFromMessages(messages));setObservation('');}
        }
      } catch(cause) {
        if(abortRef.current===controller&&!controller.signal.aborted) setObservation('连接中断，正在重新连接；'+formatBackendError(cause));
      } finally {
        if(abortRef.current===controller) {abortRef.current=null;runIdentityRef.current=null;setBusy(false);}
      }
    })();
  },[runList,conversationId,conversationLoading,applyEvent,flushDeltaBuffer]);

  const sendMessage = async () => {
    const activeEdit = editingMessage;
    const draft = activeEdit ? editText : input;
    const prompt = draft.trim() || (attachedPaths.length ? '请分析这些附件。' : '');
    if (!prompt || busy || versionBusy || uploading || workspaceFilesBusy || conversationLoading || abortRef.current) {
      return;
    }
    const attachmentBlock = attachedPaths.length
      ? `\n\n[工作区附件]\n${attachedPaths.map((path) => `- ${path}`).join('\n')}`
      : '';
    const message = `${prompt}${attachmentBlock}`;
    const controller = new AbortController();
    const localId = Date.now();
    const pendingUserId = `pending-user-${localId}`;
    const pendingAssistantId = `pending-assistant-${localId}`;
    abortRef.current = controller;
    runIdentityRef.current = {conversationId};
    streamFailureRef.current = null; streamCompletedRef.current = false;
    pendingUserBlockIdRef.current = activeEdit?.messageId ?? pendingUserId;
    pendingAssistantBlockIdRef.current = pendingAssistantId;
    pendingPromptRef.current = message;
    setInput('');
    setEditText('');
    setAttachedPaths([]);
    setEditingMessage(null);
    setBusy(true);
    setError('');
    stickToBottomRef.current = true;
    if (activeEdit?.messageId) {
      setBlocks((current) => {
        const userIndex = current.findIndex((block) => block.id === activeEdit.messageId);
        const trimmed = userIndex >= 0 ? current.slice(0, userIndex + 1) : current;
        const next = trimmed.map((block) => (
          block.id === activeEdit.messageId ? {...block, text: message} : block
        ));
        return [...next, {
          id: pendingAssistantId,
          kind: 'assistant',
          label: '助手',
          text: '',
          status: 'running',
          placeholder: true,
        }];
      });
    } else {
      setBlocks((current) => [...current, {
        id: pendingUserId,
        kind: 'user',
        label: '你',
        text: message,
      }, {
        id: pendingAssistantId,
        kind: 'assistant',
        label: '助手',
        text: '',
        status: 'running',
        placeholder: true,
      }]);
    }
    try {
      if (activeEdit?.messageId && conversationId) {
        await streamEditMessage(
          conversationId,
          activeEdit.messageId,
          message,
          {signal: controller.signal, providerId: selectedModel?.provider_id, model: selectedModel?.name, requestId: crypto.randomUUID()},
          event=>{if(abortRef.current===controller) applyEvent(event);},
        );
      } else {
        await streamChat(
          {
            message,
            workspace_id: workspaceId,
            request_id: crypto.randomUUID(),
            conversation_id: conversationId,
            title: conversationId ? undefined : '新对话',
            provider_id: selectedModel?.provider_id,
            model: selectedModel?.name,
          },
          event=>{if(abortRef.current===controller) applyEvent(event);},
          {signal: controller.signal},
        );
      }
      if (streamFailureRef.current) throw new Error(streamFailureRef.current);

    } catch (err) {
      if(abortRef.current!==controller) return;
      if(err instanceof Error&&err.name==='AbortError') return;
      setObservation('连接中断，正在重新连接；任务状态以后端为准');
      const pendingId = pendingAssistantBlockIdRef.current;
      if (pendingId) {
        setBlocks((current) => current.filter((block) => block.id !== pendingId));
        pendingAssistantBlockIdRef.current = null;
      }
      if (err instanceof Error && err.name === 'AbortError') {
        if (pendingUserBlockIdRef.current) {
          if (!activeEdit) setInput(current => current ? draft + '\n\n' + current : draft);
          setAttachedPaths(current => [...new Set([...attachedPaths, ...current])]);
        }
        setBlocks((current) => current.map((block) => (
          block.status === 'running' ? {...block, status: 'error', summary: '已停止，请核查已有结果'} : block
        )));
        setBlocks((current) => [...current, {
          id: `abort-${Date.now()}`,
          kind: 'meta',
          label: '已中止',
          text: '本次生成已停止。',
        }]);
      } else {
        const messageText = err instanceof Error ? err.message : String(err);
        setError(formatBackendError(err));
        if (err instanceof ApiError) setConflictConversationId(err.conversationId);
        if (pendingUserBlockIdRef.current) {
          if (!activeEdit) setInput(current => current ? draft + '\n\n' + current : draft);
          setAttachedPaths(current => [...new Set([...attachedPaths, ...current])]);
          const pendingUser = pendingUserBlockIdRef.current;
          setBlocks(current => current.filter(block => block.id !== pendingUser));
        }
        if (activeEdit) {
          setEditingMessage(activeEdit);
          setEditText(message);
        }

      }
    } finally {
      if(abortRef.current!==controller) return;
      flushDeltaBuffer();
      pendingPromptRef.current = '';
      pendingUserBlockIdRef.current = null;
      const completedId = runIdentityRef.current?.conversationId;
      runIdentityRef.current = null;
      if (completedId && activeConversationIdRef.current === completedId) {
        try {
          const messages = await fetchMessages(completedId);
          if (activeConversationIdRef.current === completedId && abortRef.current === controller) setBlocks(buildBlocksFromMessages(messages));
        } catch (err) { setError(formatBackendError(err)); }
      }
      abortRef.current = null; setBusy(false);
      void refreshRuns().catch(cause=>setRunError(formatBackendError(cause)));
      void refreshConversations().catch(() => {});
      void refreshWorkspaces().catch(() => {});
    }
  };

  const controlExecution=useCallback(async(id:string,action:'pause'|'resume'|'stop')=>{
    await controlRun(id,action); await refreshRuns();
  },[refreshRuns]);
  const stopGeneration = () => {
    const id=runIdentityRef.current?.runId??currentRun?.id;
    if(id) void controlExecution(id,'stop').catch(cause=>setError(formatBackendError(cause)));
    else setError('运行尚在登记，请稍后从任务列表核对；不会仅断开浏览器连接来假报停止。');
  };

  const renameConversation = async (item: ConversationSummary) => {
    const values = await askForm({
      title: '重命名会话',
      okLabel: '保存',
      fields: [{
        key: 'title',
        label: '会话标题',
        placeholder: '输入会话标题',
        value: item.title,
        required: true,
      }],
    });
    if (!values) {
      return;
    }
    const title = values.title.trim();
    if (!title || title === item.title) {
      return;
    }
    const updated = await updateConversationTitle(item.id, title);
    setConversations((current) => upsertConversation(current, updated));
  };

  const removeConversation = async (item: ConversationSummary) => {
    if (busy || versionBusy || abortRef.current) return;
    const confirmed = await askConfirm({
      title: '删除会话',
      body: `删除会话「${item.title}」？删除后不可恢复。`,
      okLabel: '删除',
      danger: true,
    });
    if (!confirmed) {
      return;
    }
    await deleteConversation(item.id);
    const next = await refreshConversations(undefined, conversationFilter);
    if (conversationId === item.id) {
      const first = next.find((conversation) => conversation.id !== item.id) ?? next[0];
      if (first) {
        await openConversation(first.id);
      } else {
        resetConversation();
      }
    }
  };

  const removeMessage = useCallback(async (messageId: string) => {
    if (!conversationId || busy || versionBusy || abortRef.current) {
      return;
    }
    const confirmed = await askConfirm({
      title: '删除消息',
      body: '删除此消息？删除后不可恢复。',
      okLabel: '删除',
      danger: true,
    });
    if (!confirmed) {
      return;
    }
    try {
      await deleteMessage(conversationId, messageId);
      setBlocks(buildBlocksFromMessages(await fetchMessages(conversationId)));
      if (editingMessage?.messageId === messageId) {
        setEditingMessage(null);
        setEditText('');
      }
      notify('已删除消息');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [conversationId, editingMessage]);

  const regenerateLast = useCallback(async (targetMessageId?: string) => {
    if (!conversationId || busy || abortRef.current) {
      return;
    }
    const lastUserBlock = [...blocks].reverse().find(
      (block) => block.kind === 'user' && block.messageId,
    );
    if (!lastUserBlock) {
      notify('没有可重新生成的消息');
      return;
    }
    flushDeltaBuffer();
    const userIndex = blocks.findIndex((block) => block.id === lastUserBlock.id);
    const trimmed = userIndex >= 0 ? blocks.slice(0, userIndex + 1) : blocks;
    const localId = Date.now();
    const pendingAssistantId = `pending-assistant-${localId}`;
    pendingAssistantBlockIdRef.current = pendingAssistantId;
    setBlocks([
      ...trimmed,
      {
        id: pendingAssistantId,
        kind: 'assistant',
        label: '助手',
        text: '',
        status: 'running',
        placeholder: true,
      },
    ]);
    const controller = new AbortController();
    abortRef.current = controller;
    runIdentityRef.current = {conversationId};
    streamFailureRef.current = null; streamCompletedRef.current = false;
    setBusy(true);
    setError('');
    try {
      await streamRegenerate(
        conversationId,
        {messageId: lastUserBlock.messageId, signal: controller.signal, providerId: selectedModel?.provider_id, model: selectedModel?.name, requestId: crypto.randomUUID()},
        event=>{if(abortRef.current===controller) applyEvent(event);},
      );
      if (streamFailureRef.current) throw new Error(streamFailureRef.current);

    } catch (err) {
      if(abortRef.current!==controller||(err instanceof Error&&err.name==='AbortError')) return;
      setObservation('连接中断，正在重新连接；任务状态以后端为准');
      const pendingId = pendingAssistantBlockIdRef.current;
      if (pendingId) {
        setBlocks((current) => current.filter((block) => block.id !== pendingId));
        pendingAssistantBlockIdRef.current = null;
      }
      if (err instanceof Error && err.name === 'AbortError') {
        setBlocks((current) => current.map((block) => (
          block.status === 'running' ? {...block, status: 'error', summary: '已停止，请核查已有结果'} : block
        )));
        setBlocks((current) => [...current, {
          id: `abort-${Date.now()}`,
          kind: 'meta',
          label: '已中止',
          text: '本次生成已停止。',
        }]);
      } else {
        const messageText = err instanceof Error ? err.message : String(err);
        setError(messageText);

      }
    } finally {
      if(abortRef.current!==controller) return;
      flushDeltaBuffer();
      setBusy(false);
      abortRef.current = null;
      void refreshRuns().catch(cause=>setRunError(formatBackendError(cause)));
      runIdentityRef.current = null;
      try { const messages = await fetchMessages(conversationId); if (activeConversationIdRef.current === conversationId && !abortRef.current) setBlocks(buildBlocksFromMessages(messages)); } catch (err) { setError(formatBackendError(err)); }
      void refreshWorkspaces().catch(() => {});
    }
  }, [applyEvent, blocks, busy, conversationId, flushDeltaBuffer, selectedModel]);

  const switchAssistantVersion = useCallback(async (messageId: string, direction: -1 | 1) => {
    if (!conversationId || busy || versionBusy || abortRef.current) {
      return;
    }
    const answerBlock = blocks.find(
      (block) => block.kind === 'assistant' && block.messageId === messageId,
    );
    if (!answerBlock) {
      return;
    }
    const target = (answerBlock.versionNumber ?? 1) + direction;
    if (target < 1 || target > (answerBlock.versionCount ?? 1)) {
      return;
    }
    setVersionBusy(true);
    setError('');
    try {
      const messages = await activateMessageVersion(conversationId, messageId, target);
      setBlocks(buildBlocksFromMessages(messages));
      await refreshConversations(conversationId, conversationFilter);
      notify(`已切换到版本 ${target}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setVersionBusy(false);
    }
  }, [blocks, busy, conversationFilter, conversationId, refreshConversations, versionBusy]);

  const exportConversation = useCallback(async (conversation: ConversationSummary, format: ExportFormat = 'markdown') => {
    try {
      const {filename, blob} = await downloadConversation(conversation.id, format);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      notify(`已导出 ${filename}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const handleShutdownStack = useCallback(async () => {
    if (!backendOnline || shutdownPending) {
      return;
    }
    const confirmed = await askConfirm({
      title: '退出并停止全部服务？',
      body: '股票任务会先收尾，工具容器与聊天服务都会停止。重新使用请再次双击启动。',
      okLabel: '退出',
      danger: true,
    });
    if (!confirmed) {
      return;
    }
    setShutdownPending(true);
    try {
      await shutdownStack();
      setBackendOnline(false);
      setStackStopped(true);
    } catch (err) {
      notify(formatBackendError(err), 'error');
      setBackendOnline(false);
      setShutdownPending(false);
    }
  }, [backendOnline, shutdownPending]);

  const handleUpload = useCallback((files: FileList) => {
    const list = Array.from(files);
    setUploading(true);
    void (async () => {
      for (const file of list) {
        try {
          const response = await uploadWorkspaceFile(file, workspaces.find(w => w.id === workspaceId)?.primary_path ?? '/workspace', workspaceId);
          attachWorkspacePath(response.attachment.workspace_path);
        } catch (err) {
          notify(err instanceof Error ? err.message : String(err), 'error');
        }
      }
      setUploading(false);
    })();
  }, [attachWorkspacePath, workspaceId, workspaces]);

  const handlePanelError = useCallback((message: string) => {
    notify(message, 'error');
  }, []);

  const openConversationReport = useCallback((report: ReadingReport) => {
    reportTriggerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    readerReturnToSheetRef.current = false;
    setSheetOpen(false);
    setReaderZoomed(true);
    setReadingReport(report.sourceMessageId ? reportReadingPositionsRef.current.get(report.sourceMessageId) ?? report : report);
  }, []);

  const briefSequence = useRef(0);
  useEffect(() => { ++briefSequence.current; }, [workspaceId, conversationId]);
  const openWorkspaceBrief = useCallback(async (path: string) => {
    const sequence = ++briefSequence.current;
    try {
      const file = await fetchWorkspacePreview(path, 1_000_000, workspaceId);
      if (sequence !== briefSequence.current) return;
      const brief = prepareHtmlBrief(file, workspaceId);
      openConversationReport({title: brief.title, content: '', htmlBrief: brief});
    } catch (cause) {
      if (sequence === briefSequence.current) notify(cause instanceof Error ? cause.message : '简报读取失败', 'error');
    }
  }, [workspaceId, openConversationReport]);

  const rememberReportPage = useCallback((report: ReadingReport) => {
    if (report.sourceMessageId) reportReadingPositionsRef.current.set(report.sourceMessageId, report);
  }, []);

  const deliverBrief = useCallback(async (path: string) => {
    if (!conversationId) throw new Error('请先打开接收简报的对话');
    const target = conversationId;
    const message = await deliverWorkspaceBrief(target, path);
    if (activeConversationIdRef.current === target) {
      setBlocks(current => current.some(block => block.messageId === message.id)
        ? current : [...current, ...buildBlocksFromMessages([message])]);
      setSheetOpen(false);
      notify('简报已交付，可直接在本对话阅读', 'ok');
    }
    await refreshConversations();
  }, [conversationId, refreshConversations]);

  const handleZoomDoc = () => {
    if (!sheetDoc) {
      return;
    }
    reportTriggerRef.current = null;
    readerReturnToSheetRef.current = true;
    setSheetOpen(false);
    setReaderZoomed(false);
    setReadingReport(sheetDoc);
  };

  const toggleReaderZoom = () => {
    setReaderZoomed((current) => !current);
  };

  const openSettings = (tab: SettingsTab) => {
    setSettingsTab(tab);
    setSettingsOpen(true);
  };

  const handleExportCurrent = (format: ExportFormat) => {
    if (activeConversation) {
      void exportConversation(activeConversation, format);
    }
  };

  const handleExportById = (id: string) => {
    const item = conversations.find((conversation) => conversation.id === id);
    if (item) {
      void exportConversation(item, 'markdown');
    }
  };

  const handleSettingsSaved = useCallback(async () => {
    const [nextModels, nextProviders, nextStatus] = await Promise.all([fetchModels(), fetchProviders(), fetchStatus(undefined, workspaceIdRef.current)]);
    setModels(nextModels);
    setProviders(nextProviders);
    setStatus(nextStatus);
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty('--ui-font', effectiveAppearance.uiFont);
    root.style.setProperty('--ui-size', `${effectiveAppearance.contentFontSize}px`);
    root.style.setProperty('--ui-leading', String(effectiveAppearance.lineHeight));
    root.style.setProperty('--code-font', effectiveAppearance.codeFont);
    root.style.setProperty('--code-size', `${effectiveAppearance.codeFontSize}px`);
    root.style.setProperty('--read-width', `${effectiveAppearance.readingWidth}px`);
    const scale = densityScale(effectiveAppearance.density);
    for (const name of DENSITY_CSS_VARS) {
      if (scale === 1) {
        root.style.removeProperty(name);
      } else {
        root.style.setProperty(name, `calc(${DENSITY_BASE_PX[name]}px * ${scale})`);
      }
    }
    return () => {
      for (const name of [...APPEARANCE_CSS_VARS, ...DENSITY_CSS_VARS]) {
        root.style.removeProperty(name);
      }
    };
  }, [effectiveAppearance]);

  useEffect(() => {
    writeJson(STORAGE_KEYS.appearance, effectiveAppearance);
  }, [effectiveAppearance]);

  useLayoutEffect(() => watchTheme(effectiveAppearance.theme), [effectiveAppearance.theme]);

  useEffect(() => {
    writeString(STORAGE_KEYS.sheetWidth, String(sheetWidth));
  }, [sheetWidth]);

  useEffect(() => {
    const timer = window.setInterval(() => { void refreshWorkspaces().catch(() => {}); }, 5000);
    return () => window.clearInterval(timer);
  }, [refreshWorkspaces]);

  useEffect(() => { persistDraft(); }, [input, attachedPaths, conversationId, workspaceId, persistDraft]);
  useEffect(() => { writeString('local-ai-agent.workspace', workspaceId); void refreshConversations().catch(() => {}); }, [workspaceId, refreshConversations]);
  const selectWorkspace = useCallback((id: string) => {
    if (uploading || workspaceFilesBusy || versionBusy) return;
    detachObserver();
    persistDraft(); ++loadSequenceRef.current; ++searchSequenceRef.current;
    workspaceIdRef.current = id; activeConversationIdRef.current = null;
    setConversationLoading(false);
    setWorkspaceId(id); setConversationId(null); setCurrentSummary(undefined); setBlocks([]);
    setConversationFilter(''); setEditingMessage(null); setEditText(''); setError('');
    setSheetDoc(null); setSheetView('workbench'); closeReportReader(false); restoreDraft(null, id);
    void fetchStatus(undefined, id).then(setStatus).catch(err => setError(formatBackendError(err)));
  }, [closeReportReader, detachObserver, persistDraft, restoreDraft, uploading, workspaceFilesBusy, versionBusy]);

  /* 工作台打开时把内容区右边界让出 Sheet 宽度：顶栏、正文、输入胶囊、通告条共同消费。
     Sheet 只负责自己的 --sheet-w，此处是 App 侧的布局让位。 */
  useEffect(() => {
    const root = document.documentElement;
    const update = () => root.style.setProperty('--sheet-inset', sheetOpen && window.innerWidth - sheetWidth >= 560 ? `${sheetWidth}px` : '0px');
    update(); window.addEventListener('resize', update);
    return () => {
      window.removeEventListener('resize', update);
      root.style.removeProperty('--sheet-inset');
    };
  }, [sheetOpen, sheetWidth]);

  useEffect(() => {
    if (!draftHydratedRef.current) return;
    activeConversationIdRef.current = conversationId;
    if (conversationId) {
      writeString(STORAGE_KEYS.activeConversation, conversationId);
    } else {
      window.localStorage.removeItem(STORAGE_KEYS.activeConversation);
    }
  }, [conversationId]);

  useEffect(() => {
    void loadBootstrap();
  }, [loadBootstrap]);

  /* First establish the HttpOnly cookie; EventSource cannot carry headers. */
  useEffect(() => {
    let disposed = false;
    let source: EventSource | undefined;
    void ensureBrowserSession().then(() => {
      if (!disposed) source = new EventSource(`${DEFAULT_BASE_URL}/api/keepalive`, {withCredentials: true});
    }).catch(() => {});
    return () => { disposed = true; source?.close(); };
  }, []);

  useEffect(() => {
    if (loading) {
      return;
    }
    const handle = window.setTimeout(() => {
      void refreshConversations(conversationId, conversationFilter).catch((err) => {
        setBackendOnline(false);
        setError(formatBackendError(err));
      });
    }, CONVERSATION_REFRESH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [conversationFilter, conversationId, loading, refreshConversations]);

  useEffect(() => {
    return () => {
      if (deltaTimerRef.current !== null) {
        window.clearTimeout(deltaTimerRef.current);
      }
      deltaTimerRef.current = null;
      deltaBufferRef.current = {};
      pendingPromptRef.current = '';
      pendingUserBlockIdRef.current = null;
      pendingAssistantBlockIdRef.current = null;
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, []);

  /* 滚动容器 #transcript：同一个监听维护粘性跟随开关与顶栏分隔线 */
  useEffect(() => {
    const node = document.getElementById('transcript');
    if (!node) {
      return;
    }
    const topbar = document.querySelector('.topbar');
    const handleScroll = () => {
      stickToBottomRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < STICKY_BOTTOM_THRESHOLD;
      topbar?.classList.toggle('scrolled', node.scrollTop > TOPBAR_SCROLLED_OFFSET);
    };
    handleScroll();
    node.addEventListener('scroll', handleScroll, {passive: true});
    return () => node.removeEventListener('scroll', handleScroll);
  }, []);

  useEffect(() => {
    if (suppressNextAutoScrollRef.current) {
      suppressNextAutoScrollRef.current = false;
      return;
    }
    if (!stickToBottomRef.current) {
      return;
    }
    const node = document.getElementById('transcript');
    node?.scrollTo({top: node.scrollHeight, behavior: 'smooth'});
  }, [blocks, busy]);

  /* Escape 逐层退出；确认弹层与浮层各自处理，App 让位 */
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || pendingDialog) {
        return;
      }
      if (readingReport) {
        closeReportReader();
        return;
      }
      if (settingsOpen) {
        setSettingsOpen(false);
        return;
      }
      if (sheetOpen && sheetView === 'doc') {
        setSheetView('workbench');
        return;
      }
      if (sheetOpen) {
        setSheetOpen(false);
        return;
      }
      if (drawerOpen) {
        setDrawerOpen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [closeReportReader, drawerOpen, pendingDialog, readingReport, settingsOpen, sheetOpen, sheetView]);

  const composerStatus = busy
    ? (currentRun ? runLabel(currentRun.status) : '正在提交…')
    : versionBusy
      ? '正在切换版本…'
      : uploading || workspaceFilesBusy
        ? '正在处理工作区文件…'
      : selectedModel
        ? `${selectedModel.provider_name} · ${selectedModel.name}`
        : '未选择模型';

  return (
    <WorkspaceContext.Provider value={workspaceId}>
    <BriefReaderContext.Provider value={openWorkspaceBrief}>
    <ToolNamesContext.Provider value={Object.fromEntries((status?.tool_catalog ?? []).map(tool => [tool.name, tool.display_name]))}>
      <IconSprite />
      <TopBar
        runningCount={runList.runs.filter(r=>['RUNNING','PAUSING','STOPPING'].includes(r.status)).length}
        pausedCount={runList.runs.filter(r=>r.status==='PAUSED').length}
        onOpenRuns={()=>{setSheetView('runs');setSheetOpen(true);}}
        title={activeConversation?.title ?? (conversationId ? '当前会话' : '新对话')}
        onOpenDrawer={() => setDrawerOpen(true)}
        providers={providers}
        value={selectedModelId}
        onChangeModel={setSelectedModelId}
        onRefreshModels={refreshModels}
        modelsRefreshing={modelsRefreshing}
        exportEnabled={Boolean(conversationId)}
        onExport={handleExportCurrent}
        onOpenModelSettings={() => openSettings('model')}
        sheetOpen={sheetOpen}
        onToggleSheet={() => setSheetOpen((current) => !current)}
        onOpenSettings={() => openSettings('appearance')}
      />
      <Transcript
        runStatus={currentRun?.status}
        items={transcriptItems}
        busy={busy}
        loading={loading}
        conversationLoading={conversationLoading}
        error={error || null}
        backendOnline={backendOnline}
        onDismissError={() => setError('')}
        onReconnect={() => { void loadBootstrap(); }}
        onExample={(text) => setInput(text)}
        onToggleBlock={toggleBlock}
        onCopy={(text) => { void handleCopy(text); }}
        onEdit={beginEditMessage}
        onDelete={(messageId) => { void removeMessage(messageId); }}
        onRegenerate={(messageId) => { void regenerateLast(messageId); }}
        onSwitchVersion={(messageId, direction) => { void switchAssistantVersion(messageId, direction); }}
        onReadReport={openConversationReport}
        editingMessageId={editingMessage?.messageId ?? null}
        onCancelEdit={cancelEditMessage}
        editText={editText}
        onEditTextChange={setEditText}
        onSaveEdit={() => { void sendMessage(); }}
        onOpenConversation={openConversation}
      />
      {conflictConversationId && <div className="notice-bar" role="status"><button className="btn btn-quiet" onClick={() => void openConversation(conflictConversationId)}>查看正在执行的会话</button></div>}
      <Composer
        runStatus={currentRun&&<section className="conversation-run" aria-label="当前会话运行状态">
          <span role="status">{runLabel(currentRun.status)} · {currentRun.phase||'准备中'}{observation?' · '+observation:''}</span>
          <RunControls run={currentRun} onControl={controlExecution}/>
        </section>}
        contextLoading={loading || conversationLoading}
        value={editingMessage ? editText : input}
        onChange={editingMessage ? setEditText : setInput}
        onSend={() => { void sendMessage(); }}
        onStop={stopGeneration}
        busy={busy}
        versionBusy={versionBusy || uploading || workspaceFilesBusy || conversationLoading || loading}
        editing={editingMessage !== null}
        onCancelEdit={cancelEditMessage}
        attachments={attachedPaths}
        onDetach={detachWorkspacePath}
        onUpload={handleUpload}
        statusText={composerStatus}
        placeholder={editingMessage
          ? '编辑这条用户消息。发送后会重新生成后续回答。'
          : '输入消息。可以从工作台附加文件。Enter 发送，Shift+Enter 换行。'}
      />
      <ConversationDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        conversations={conversations}
        workspaces={workspaces} workspaceId={workspaceId} onSelectWorkspace={selectWorkspace}
        busy={versionBusy || uploading || workspaceFilesBusy || conversationLoading || loading}
        activeId={conversationId}
        filter={conversationFilter}
        onFilterChange={setConversationFilter}
        onOpen={(id) => {
          setDrawerOpen(false);
          void openConversation(id);
        }}
        onNew={() => {
          setDrawerOpen(false);
          resetConversation();
        }}
        onRename={(item) => { void renameConversation(item).catch(err => notify(formatBackendError(err), 'error')); }}
        onDelete={(item) => { void removeConversation(item).catch(err => notify(formatBackendError(err), 'error')); }}
        onExport={handleExportById}
        backendOnline={backendOnline}
        onShutdown={() => { void handleShutdownStack(); }}
        shutdownPending={shutdownPending}
      />
      <Sheet
        runs={{data:runList,error:runError,onOpen:id=>{setSheetOpen(false);void openConversation(id);},onControl:controlExecution}}
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        view={sheetView}
        onViewChange={setSheetView}
        width={sheetWidth}
        onWidthChange={setSheetWidth}
        doc={sheetDoc}
        onZoomDoc={handleZoomDoc}
        workspaceManager={{workspace: workspaces.find(w => w.id === workspaceId), workspaces, busy: versionBusy || uploading || workspaceFilesBusy || loading || conversationLoading, onSelect: selectWorkspace, onRefresh: refreshWorkspaces, onOpenConversation: openConversation}}
        workspace={{
          workspaceId, roots: workspaces.find(w => w.id === workspaceId)?.roots ?? [], primaryPath: workspaces.find(w => w.id === workspaceId)?.primary_path,
          busy: busy || versionBusy || loading || conversationLoading || uploading,
          onBusyChange: setWorkspaceFilesBusy,
          attachedPaths,
          onAttach: attachWorkspacePath,
          onDetach: detachWorkspacePath,
          onError: handlePanelError,
          onReadBrief: openWorkspaceBrief,
          onDeliverBrief: conversationId ? deliverBrief : undefined,
        }}
        skills={{
          conversation: activeConversation,
          onChanged: () => { void handleSettingsSaved(); },
          onMethodChanged: (updated) => setConversations((current) => current.map((item) => (
            item.id === updated.id ? updated : item
          ))),
        }}
      />
      <Reader
        report={readingReport}
        conversationId={conversationId ?? undefined}
        onPageRead={rememberReportPage}
        onClose={closeReportReader}
        zoomed={readerZoomed}
        onToggleZoom={toggleReaderZoom}
      />
      <SettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        initialTab={settingsTab}
        appearance={effectiveAppearance}
        onAppearanceChange={updateAppearance}
        onAppearanceReset={resetAppearance}
        model={selectedModel}
        status={status}
        providers={providers}
        busy={busy}
        onSaved={handleSettingsSaved}
      />
      <ConfirmDialog />
      <Toast />
      {stackStopped ? (
        <div className="stopped" role="alert">
          <div className="stopped-card">
            <span className="empty-mark" aria-hidden="true"><Icon name="i-check" className="ico-lg" /></span>
            <h2>已停止运行</h2>
            <p>
              股票任务已收尾，工具容器与聊天服务都已停止。重新使用请双击
              <span className="mono">启动.bat</span>，或运行 <span className="mono">scripts/start-daily.ps1</span>。
            </p>
            <button type="button" className="btn btn-secondary" onClick={() => window.location.reload()}>
              重新加载页面
            </button>
          </div>
        </div>
      ) : null}
    </ToolNamesContext.Provider>
    </BriefReaderContext.Provider>
    </WorkspaceContext.Provider>
  );
}
