import {
  useEffect,
  useId,
  useRef,
  useState,
  type DragEvent,
  type KeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";

import { TEXT } from "../../text";
import { LiveRegion } from "./LiveRegion";

/** Mudança de posição na lista, em índices a partir de 0. */
export interface ItemMove {
  readonly from: number;
  readonly to: number;
}

/** Cópia da lista com o item de `from` em `to`; posição fora da lista devolve a cópia igual. */
export function moveItem<T>(items: readonly T[], { from, to }: ItemMove): T[] {
  const copy = [...items];
  if (!isIndexOf(items, from) || !isIndexOf(items, to)) {
    return copy;
  }
  const [moved] = copy.splice(from, 1);
  if (moved !== undefined) {
    copy.splice(to, 0, moved);
  }
  return copy;
}

type Direction = "up" | "down";

const STEP: Record<Direction, number> = { up: -1, down: 1 };
const DIRECTION_TEXT: Record<Direction, string> = {
  up: TEXT.reorder.moveUp,
  down: TEXT.reorder.moveDown,
};
const SHORTCUT_KEYS: Readonly<Record<string, Direction>> = { ArrowUp: "up", ArrowDown: "down" };

interface ReorderableListProps<T> {
  /** Nome acessível da lista. */
  readonly label: string;
  readonly items: readonly T[];
  readonly getKey: (item: T) => string;
  /** Nome curto do item, usado nos botões e no anúncio. */
  readonly getName: (item: T) => string;
  readonly renderItem: (item: T) => ReactNode;
  readonly onMove: (move: ItemMove) => void;
}

/**
 * Lista ordenada que se reordena por arrasto e, sempre, sem ele (WCAG 2.5.7): botões Mover para
 * cima e Mover para baixo em cada item, o atalho Alt + seta com o foco num desses botões, e o
 * anúncio da nova posição numa região `aria-live`. O foco segue o item movido.
 */
export function ReorderableList<T>({
  label,
  items,
  getKey,
  getName,
  renderItem,
  onMove,
}: ReorderableListProps<T>): ReactElement {
  const instructionsId = useId();
  const [announcement, setAnnouncement] = useState<string | null>(null);
  const [dragged, setDragged] = useState<number | null>(null);
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const pendingFocus = useRef<string | null>(null);

  useEffect(() => {
    // Reordenar move nós do DOM, e o navegador tira o foco de quem é movido: devolve-o ao botão.
    if (pendingFocus.current !== null) {
      buttons.current.get(pendingFocus.current)?.focus();
      pendingFocus.current = null;
    }
  });

  /** Move o item e devolve o foco a `focusDirection` do item; nada acontece fora da lista. */
  const move = (from: number, to: number, focusDirection: Direction | null = null): void => {
    const item = items[from];
    if (item === undefined || !isIndexOf(items, to) || from === to) {
      return;
    }
    // Só grava o foco pendente quando há render a seguir: senão ele ficaria preso e roubaria o
    // foco no próximo render, vindo de qualquer outra mudança da página.
    if (focusDirection !== null) {
      pendingFocus.current = buttonKey(getKey(item), focusDirection);
    }
    onMove({ from, to });
    setAnnouncement(TEXT.reorder.moved(getName(item), to + 1, items.length));
  };

  const moveByButton = (index: number, direction: Direction): void => {
    move(index, index + STEP[direction], direction);
  };

  const onShortcut = (index: number, event: KeyboardEvent<HTMLButtonElement>): void => {
    const direction = SHORTCUT_KEYS[event.key];
    if (!event.altKey || direction === undefined) {
      return;
    }
    event.preventDefault();
    const focused = event.currentTarget.dataset["direction"] === "down" ? "down" : "up";
    move(index, index + STEP[direction], focused);
  };

  const dragHandlers = (index: number) => ({
    draggable: true,
    onDragStart: (event: DragEvent<HTMLLIElement>) => {
      event.dataTransfer.effectAllowed = "move";
      // O Firefox só inicia o arrasto com algum dado no dataTransfer.
      event.dataTransfer.setData("text/plain", String(index));
      setDragged(index);
    },
    onDragOver: (event: DragEvent<HTMLLIElement>) => {
      if (dragged !== null) {
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
      }
    },
    onDrop: (event: DragEvent<HTMLLIElement>) => {
      event.preventDefault();
      if (dragged !== null) {
        move(dragged, index);
      }
      setDragged(null);
    },
    onDragEnd: () => {
      setDragged(null);
    },
  });

  const registerButton = (key: string) => (element: HTMLButtonElement | null) => {
    if (element === null) {
      buttons.current.delete(key);
    } else {
      buttons.current.set(key, element);
    }
  };

  return (
    <div>
      <p id={instructionsId}>{TEXT.reorder.instructions}</p>
      <ol aria-label={label} aria-describedby={instructionsId}>
        {items.map((item, index) => {
          const key = getKey(item);
          const name = getName(item);
          return (
            <li key={key} {...dragHandlers(index)}>
              {renderItem(item)}
              {(["up", "down"] as const).map((direction) => (
                <button
                  key={direction}
                  ref={registerButton(buttonKey(key, direction))}
                  type="button"
                  data-direction={direction}
                  // Começa pelo texto visível (WCAG 2.5.3) e acrescenta o item, que o texto não diz.
                  aria-label={`${DIRECTION_TEXT[direction]} ${name}`}
                  aria-disabled={!isIndexOf(items, index + STEP[direction])}
                  aria-keyshortcuts={direction === "up" ? "Alt+ArrowUp" : "Alt+ArrowDown"}
                  onClick={() => {
                    moveByButton(index, direction);
                  }}
                  onKeyDown={(event) => {
                    onShortcut(index, event);
                  }}
                >
                  {DIRECTION_TEXT[direction]}
                </button>
              ))}
            </li>
          );
        })}
      </ol>
      <LiveRegion message={announcement} visuallyHidden />
    </div>
  );
}

function buttonKey(itemKey: string, direction: Direction): string {
  return `${itemKey}:${direction}`;
}

function isIndexOf(items: readonly unknown[], index: number): boolean {
  return Number.isInteger(index) && index >= 0 && index < items.length;
}
