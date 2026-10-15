/**
 * Shared controls built on the design tokens. Every interactive element has a
 * 44×44 px minimum hit area (Apple HIG), a visible keyboard focus ring (global
 * :focus-visible in index.css), and states for hover / active / disabled /
 * loading expressed in more than colour alone.
 */
import {
  forwardRef,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent-strong text-on-accent hover:brightness-[1.08] active:brightness-95",
  secondary: "bg-surface text-fg border border-line-strong hover:border-accent hover:text-accent-strong",
  ghost: "bg-transparent text-accent-strong hover:bg-accent-soft",
  danger: "bg-transparent text-danger border border-danger hover:bg-danger-soft",
};

export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <span
      aria-hidden="true"
      className="inline-block shrink-0 rounded-full border-2 border-current border-r-transparent"
      style={{ width: size, height: size, animation: "fw-spin 0.8s linear infinite" }}
    />
  );
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  loading?: boolean;
  icon?: ReactNode;
  size?: "md" | "lg";
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", loading = false, icon, size = "md", className = "", children, disabled, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={`inline-flex min-h-11 items-center justify-center gap-2 rounded-md font-semibold whitespace-nowrap
        transition-[background-color,border-color,color,filter,transform] duration-150 active:scale-[0.98]
        disabled:cursor-not-allowed disabled:opacity-45 disabled:active:scale-100
        ${size === "lg" ? "px-6 text-base" : "px-4 text-[15px]"} ${VARIANTS[variant]} ${className}`}
      {...props}
    >
      {loading ? <Spinner /> : icon}
      {children}
    </button>
  );
});

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  pressed?: boolean;
}

/** Square 44×44 button whose only content is an icon; `label` is its name. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, pressed, className = "", children, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      className={`inline-flex h-11 min-w-11 items-center justify-center gap-1.5 rounded-md px-2.5 text-sm font-medium
        transition-colors duration-150 disabled:opacity-45
        ${pressed ? "bg-accent-soft text-accent-strong" : "text-fg hover:bg-accent-soft hover:text-accent-strong"} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
});

export interface SegmentOption<T extends string> {
  value: T;
  label: ReactNode;
  /** Accessible name when `label` is an icon. */
  ariaLabel?: string;
}

/** Segmented control (a set of mutually exclusive toggle buttons). */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
  size = "md",
  className = "",
}: {
  options: SegmentOption<T>[];
  value: T;
  onChange: (value: T) => void;
  label: string;
  size?: "sm" | "md";
  className?: string;
}) {
  return (
    <div role="group" aria-label={label} className={`inline-flex rounded-md border border-line bg-surface p-0.5 ${className}`}>
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            aria-label={option.ariaLabel}
            title={option.ariaLabel}
            onClick={() => onChange(option.value)}
            className={`inline-flex items-center justify-center gap-1.5 rounded-[8px] font-medium whitespace-nowrap transition-colors duration-150
              ${size === "sm" ? "min-h-9 min-w-9 px-2.5 text-[13px]" : "min-h-10 min-w-10 px-3 text-sm"}
              ${active ? "bg-accent-soft text-accent-strong" : "text-muted hover:text-fg"}`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/**
 * A button that opens a small floating panel (speed, volume, more…).
 * Closes on Escape, outside click, or when focus leaves it.
 */
export function Popover({
  trigger,
  label,
  children,
  align = "center",
  placement = "top",
}: {
  trigger: (props: { open: boolean; toggle: () => void; id: string }) => ReactNode;
  label: string;
  children: ReactNode | ((close: () => void) => ReactNode);
  align?: "start" | "center" | "end";
  placement?: "top" | "bottom";
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const close = () => setOpen(false);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        (rootRef.current?.querySelector("button") as HTMLButtonElement | null)?.focus();
      }
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const horizontal = align === "start" ? "left-0" : align === "end" ? "right-0" : "left-1/2 -translate-x-1/2";
  const vertical = placement === "top" ? "bottom-full mb-2" : "top-full mt-2";

  return (
    <div ref={rootRef} className="relative">
      {trigger({ open, toggle: () => setOpen((o) => !o), id })}
      {open && (
        <div
          id={id}
          role="dialog"
          aria-label={label}
          className={`absolute z-30 ${vertical} ${horizontal} min-w-56 rounded-lg border border-line bg-raised p-3 shadow-float`}
        >
          {typeof children === "function" ? children(close) : children}
        </div>
      )}
    </div>
  );
}

/** Inline alert box for errors and warnings. */
export function Alert({ title, children, tone = "danger" }: { title: string; children?: ReactNode; tone?: "danger" | "warm" }) {
  const color = tone === "danger" ? "border-danger bg-danger-soft" : "border-warm bg-surface";
  const titleColor = tone === "danger" ? "text-danger" : "text-warm";
  return (
    <div role="alert" className={`grid gap-1 rounded-md border px-4 py-3 ${color}`}>
      <p className={`font-semibold ${titleColor}`}>{title}</p>
      {children && <div className="text-[15px] text-fg">{children}</div>}
    </div>
  );
}

/** Keyboard key cap. */
export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex min-w-7 justify-center rounded-[6px] border border-b-2 border-line-strong bg-surface px-1.5 py-0.5 font-mono text-xs text-fg">
      {children}
    </kbd>
  );
}
