/** Versioned page-adapter table. Model and effort names are whatever the page
 * shows. This module does not enumerate product model ids.
 *
 * Hosts that drive a non-Playwright browser should follow `interaction`:
 * menu items accept element.click; the model trigger needs a full pointer and
 * mouse sequence. Coordinate clicks on an open menu are refused by the page.
 */
const COMPOSER = '[role="textbox"][contenteditable="true"]';
const SEND_BUTTON = 'button[aria-label="发送"]';

export const PAGE_ADAPTER_VERSION = 1;

export const CHATGPT_ORDINARY_CHAT_ZH = Object.freeze({
  id: 'chatgpt-ordinary-chat-zh',
  version: PAGE_ADAPTER_VERSION,
  observed: '2026-10-22',
  locale: 'zh-CN',
  completionProfile: 'chatgpt-turn-dom-v1',
  composer: COMPOSER,
  sendButton: SEND_BUTTON,
  labels: Object.freeze({
    chat: '聊天',
    work: '工作',
    modelPicker: '选择 ChatGPT 模型',
    modelMenu: '选择模型',
    effortMenu: '强度',
    send: '发送',
    login: Object.freeze(['登录', 'Log in', 'Sign in']),
  }),
  selectors: Object.freeze({
    composer: COMPOSER,
    sendButton: SEND_BUTTON,
  }),
  roles: Object.freeze({
    button: 'button',
    menuItem: 'menuitem',
    modelOption: 'menuitemradio',
  }),
  interaction: Object.freeze({
    menuItem: 'element.click',
    modelTrigger: 'pointer-and-mouse-sequence',
    avoidCoordinateClickOnMenu: true,
    fill: 'paste-then-verify',
    readBack: 'innerText',
  }),
});

export const PAGE_ADAPTERS = Object.freeze({
  [CHATGPT_ORDINARY_CHAT_ZH.id]: CHATGPT_ORDINARY_CHAT_ZH,
});

export function pageAdapterById(id = CHATGPT_ORDINARY_CHAT_ZH.id) {
  const found = PAGE_ADAPTERS[id];
  if (!found || found.version !== PAGE_ADAPTER_VERSION) throw Error('PAGE_ADAPTER_NOT_FOUND');
  return found;
}
