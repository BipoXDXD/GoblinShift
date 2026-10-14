import type { ComponentPropsWithoutRef, MouseEvent, ReactElement } from "react";

/** Pirâmide de ações (RUI cap. 2): uma primária sólida por tela; destrutiva só na confirmação. */
export type ButtonVariant = "primary" | "secondary" | "tertiary" | "destructive";
export type ButtonSize = "md" | "lg";

interface ButtonLook {
  readonly variant: ButtonVariant;
  readonly size?: ButtonSize;
  /** Ocupa a largura do contêiner (Executar no celular). */
  readonly fullWidth?: boolean;
}

const BASE =
  "inline-flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-lg font-semibold no-underline transition aria-disabled:cursor-not-allowed";

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-primary text-on-primary shadow-1 hover:bg-primary-hover active:shadow-none aria-disabled:bg-surface-sunken aria-disabled:text-fg-subtle aria-disabled:shadow-none",
  secondary:
    "border-2 border-secondary-fg bg-surface text-secondary-fg shadow-1 hover:bg-surface-sunken active:shadow-none aria-disabled:border-line aria-disabled:text-fg-subtle aria-disabled:shadow-none",
  tertiary:
    "text-link underline underline-offset-4 hover:decoration-2 aria-disabled:text-fg-subtle",
  destructive:
    "bg-destructive text-on-destructive shadow-1 hover:bg-destructive-hover active:shadow-none aria-disabled:bg-surface-sunken aria-disabled:text-fg-subtle aria-disabled:shadow-none",
};

// Padding por tamanho, feito à mão (RUI cap. 3): 16 px de fonte com 8 × 16 dá 44 px de altura.
const SIZES: Record<ButtonSize, string> = {
  md: "px-4 py-2 text-base",
  lg: "px-6 py-3 text-base",
};

/** Classes de botão, também para um link que age como botão (Baixar pacote). */
export function buttonClassName({ variant, size = "md", fullWidth = false }: ButtonLook): string {
  const tertiaryPadding = variant === "tertiary" ? "px-2" : SIZES[size];
  return [BASE, VARIANTS[variant], tertiaryPadding, fullWidth ? "w-full" : ""]
    .filter((part) => part !== "")
    .join(" ");
}

type NativeButtonProps = Omit<
  ComponentPropsWithoutRef<"button">,
  "className" | "type" | "disabled"
>;

interface ButtonProps extends NativeButtonProps, ButtonLook {
  /** Explícito: o padrão do HTML é `submit`, que envia o formulário sem querer. */
  readonly type: "button" | "submit";
  /**
   * Desabilitado sem sair da ordem de Tab (o leitor de tela ainda anuncia o botão); o clique e o
   * envio do formulário ficam bloqueados.
   */
  readonly "aria-disabled"?: boolean;
}

export function Button({
  variant,
  size,
  fullWidth,
  type,
  onClick,
  children,
  ...props
}: ButtonProps): ReactElement {
  const blocked = props["aria-disabled"] === true;
  const handleClick = (event: MouseEvent<HTMLButtonElement>): void => {
    if (blocked) {
      event.preventDefault();
      return;
    }
    onClick?.(event);
  };
  return (
    <button
      {...props}
      type={type}
      className={buttonClassName({
        variant,
        ...(size === undefined ? {} : { size }),
        ...(fullWidth === undefined ? {} : { fullWidth }),
      })}
      onClick={handleClick}
    >
      {children}
    </button>
  );
}
