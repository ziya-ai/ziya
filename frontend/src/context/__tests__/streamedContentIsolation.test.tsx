/**
 * @jest-environment jsdom
 *
 * Streaming flushes must not re-render the whole app.
 *
 * chatApi swaps in a new streamedContentMap every 150 ms (and a new
 * reasoningContentMap on every thinking delta).  Both maps used to be part
 * of the ActiveChatContext and ChatContext values, and App.tsx read the map
 * directly, so every flush re-rendered App, the sidebar, the modals and
 * every context consumer in history.  A heap profile of one 100 s stream
 * measured 18.4 GB allocated (~180 MB/s), almost all antd/MUI styling and
 * React bookkeeping from those re-renders; V8 old space filled with the
 * promoted garbage (108 -> 471 MB in 80 s) faster than it was collected.
 *
 * The maps now live in StreamedContentContext.  Pinned here:
 *   - a useActiveChat() consumer does NOT re-render on a flush, while a
 *     useStreamedContent() consumer DOES (and sees the new text);
 *   - the presence set changes only when a conversation gains or loses
 *     content, not on every flush;
 *   - the ref reader sees current content without re-rendering;
 *   - the length watcher reports every change (App's scroll path);
 *   - the seams: ChatContext mounts the provider, no non-test source reads
 *     either map off useActiveChat(), and App no longer reads the map.
 */
import * as fs from 'fs';
import * as path from 'path';
import React, { useState } from 'react';
import { render, act } from '@testing-library/react';
import { ActiveChatProvider, useActiveChat } from '../ActiveChatContext';
import {
    StreamedContentProvider, StreamedContentLengthWatcher,
    useStreamedContent, useStreamedContentPresence, useStreamedContentRef,
} from '../StreamedContentContext';

const noop = () => {};
const asyncNoop = async () => undefined as any;
// One object for the life of the test so ActiveChatProvider's memo deps are
// identical across renders: only the maps change, as during a stream.
const STABLE: any = {
    currentConversationId: 'c', currentMessages: [], setCurrentConversationId: noop,
    addMessageToConversation: noop, loadConversation: noop,
    loadConversationAndScrollToMessage: asyncNoop, startNewChat: asyncNoop,
    startNewEphemeralChat: asyncNoop, promoteEphemeralToRetained: asyncNoop,
    editingMessageIndex: null, setEditingMessageIndex: noop,
    isStreaming: true, setIsStreaming: noop,
    streamingConversations: new Set(['c']), runningTaskConversations: new Set(),
    conversationTaskBindings: new Map(),
    addStreamingConversation: noop, removeStreamingConversation: noop,
    setStreamedContentMap: noop, setReasoningContentMap: noop,
    getProcessingState: () => 'idle', updateProcessingState: noop,
    lastResponseIncomplete: false, setDisplayMode: noop, toggleMessageMute: noop,
    removeMessage: noop, replaceMessages: noop, setChatContexts: asyncNoop,
    currentDisplayMode: 'pretty', throttlingRecoveryData: new Map(),
    setThrottlingRecoveryData: noop,
};

const counts = { chrome: 0, live: 0, presence: 0, ref: 0 };
let refSeen: { readonly current: any } | null = null;
const lengths: number[] = [];
const onLength = (n: number) => { lengths.push(n); };

const Chrome = React.memo(() => { useActiveChat(); counts.chrome++; return null; });
const Live = React.memo(() => {
    const { streamedContentMap } = useStreamedContent();
    counts.live++;
    return <span data-testid="live">{streamedContentMap.get('c') ?? ''}</span>;
});
const Presence = React.memo(() => {
    const p = useStreamedContentPresence();
    counts.presence++;
    return <span data-testid="presence">{[...p].join(',')}</span>;
});
const RefReader = React.memo(() => { refSeen = useStreamedContentRef(); counts.ref++; return null; });

let setMap: (m: Map<string, string>) => void = noop;
const Harness: React.FC = () => {
    const [map, set] = useState(() => new Map<string, string>());
    setMap = set;
    const [rmap] = useState(() => new Map());
    // Mirrors ChatContext's tree.  The maps are also handed to
    // ActiveChatProvider, as ChatContext did before the split, so on the
    // unfixed provider the chrome consumer re-renders and this suite fails.
    const legacy: any = { streamedContentMap: map, reasoningContentMap: rmap };
    return (
        <ActiveChatProvider {...STABLE} {...legacy}>
            <StreamedContentProvider streamedContentMap={map} reasoningContentMap={rmap}>
                <Chrome /><Live /><Presence /><RefReader />
                <StreamedContentLengthWatcher conversationId="c" onLength={onLength} />
            </StreamedContentProvider>
        </ActiveChatProvider>
    );
};

beforeEach(() => {
    counts.chrome = counts.live = counts.presence = counts.ref = 0;
    lengths.length = 0;
    refSeen = null;
});

describe('streaming flush isolation', () => {
    it('re-renders only streamed-content consumers on a flush', () => {
        const { getByTestId } = render(<Harness />);
        const base = { ...counts };
        for (const text of ['a', 'ab', 'abc']) {
            act(() => { setMap(new Map([['c', text]])); });
        }
        expect(getByTestId('live').textContent).toBe('abc');    // the path ran
        expect(counts.live - base.live).toBe(3);
        expect(counts.chrome - base.chrome).toBe(0);            // the fix
        expect(counts.ref - base.ref).toBe(0);
        expect(refSeen!.current.streamedContentMap.get('c')).toBe('abc');
    });

    it('presence changes only when content appears or is cleared', () => {
        const { getByTestId } = render(<Harness />);
        const base = counts.presence;
        act(() => { setMap(new Map([['c', 'a']])); });
        act(() => { setMap(new Map([['c', 'ab']])); });
        act(() => { setMap(new Map([['c', 'abc'], ['other', '']])); });   // empty entry is not presence
        expect(getByTestId('presence').textContent).toBe('c');
        expect(counts.presence - base).toBe(1);
        act(() => { setMap(new Map()); });
        expect(getByTestId('presence').textContent).toBe('');
        expect(counts.presence - base).toBe(2);
    });

    it('the length watcher reports each change of the conversation\'s length', () => {
        render(<Harness />);
        act(() => { setMap(new Map([['c', 'a']])); });
        act(() => { setMap(new Map([['c', 'ab']])); });
        act(() => { setMap(new Map([['other', 'zzz'], ['c', 'ab']])); });  // unrelated conversation
        act(() => { setMap(new Map()); });
        expect(lengths).toEqual([0, 1, 2, 0]);
    });

    it('defaults to empty maps without a provider', () => {
        const { getByTestId } = render(<><Live /><Presence /></>);
        expect(getByTestId('live').textContent).toBe('');
        expect(getByTestId('presence').textContent).toBe('');
    });
});

describe('seams', () => {
    const SRC = path.join(__dirname, '..', '..');
    const read = (rel: string) => fs.readFileSync(path.join(SRC, rel), 'utf8');

    it('ChatContext mounts StreamedContentProvider and no longer feeds the maps to ActiveChat or its own value', () => {
        const src = read('context/ChatContext.tsx');
        const mountStart = src.indexOf('<ActiveChatProvider');
        const mountEnd = src.indexOf('</ActiveChatProvider>');
        expect(mountStart).toBeGreaterThan(-1);
        const mount = src.slice(mountStart, mountEnd);
        const acProps = mount.slice(0, mount.indexOf('>\n'));
        expect(acProps).not.toMatch(/\b(streamedContentMap|reasoningContentMap)=/);
        expect(mount).toMatch(/<StreamedContentProvider\s+streamedContentMap=\{streamedContentMap\}\s+reasoningContentMap=\{reasoningContentMap\}/);
        const v = src.indexOf('const value = useMemo(() => ({');
        const valueBlock = src.slice(v, src.indexOf('\n    ]);', v));
        expect(v).toBeGreaterThan(-1);
        expect(valueBlock).not.toMatch(/^\s*(streamedContentMap|reasoningContentMap),$/m);
        expect(valueBlock).toMatch(/^\s*setStreamedContentMap,$/m);       // setters stay
    });

    it('ActiveChatContext carries the setters but not the maps', () => {
        const src = read('context/ActiveChatContext.tsx');
        expect(src).not.toMatch(/value\.(streamedContentMap|reasoningContentMap)\b/);
        expect(src).toMatch(/setStreamedContentMap: value\.setStreamedContentMap/);
    });

    it('App drives scrolling through the watcher, not the map', () => {
        const src = read('components/App.tsx');
        expect(src).not.toMatch(/streamedContentMap/);
        expect(src).toMatch(/<StreamedContentLengthWatcher[\s\S]{0,120}onLength=\{notifyContentLength\}/);
    });

    it('no non-test source reads a streaming map off useActiveChat()', () => {
        const offenders: string[] = [];
        const walk = (dir: string) => {
            for (const name of fs.readdirSync(dir)) {
                const p = path.join(dir, name);
                if (name === '__tests__' || name === 'node_modules') continue;
                if (fs.statSync(p).isDirectory()) { walk(p); continue; }
                if (!/\.(tsx?|jsx?)$/.test(name)) continue;
                const s = fs.readFileSync(p, 'utf8');
                const re = /const\s*\{([^}]*)\}\s*=\s*useActiveChat(?:Optional)?\(\)/g;
                let m: RegExpExecArray | null;
                while ((m = re.exec(s))) {
                    if (/\b(streamedContentMap|reasoningContentMap)\b/.test(m[1])) offenders.push(path.relative(SRC, p));
                }
                if (/\b(ac|activeChat)\??\.(streamedContentMap|reasoningContentMap)\b/.test(s)) offenders.push(path.relative(SRC, p));
            }
        };
        walk(SRC);
        expect(offenders).toEqual([]);
        // and the live readers moved to the new context
        expect(read('components/StreamedContent.tsx')).toMatch(/useStreamedContent\(\)/);
        expect(read('components/Conversation.tsx')).toMatch(/useStreamedContentPresence\(\)/);
        expect(read('hooks/useSendPayload.ts')).toMatch(/useStreamedContentRef\(\)/);
        const mr = read('components/MarkdownRenderer.tsx');
        const live = mr.slice(mr.indexOf('const LiveThinkingBlock'), mr.indexOf('const LiveThinkingBlock') + 400);
        expect(live).toMatch(/useStreamedContent\(\)/);
    });
});
