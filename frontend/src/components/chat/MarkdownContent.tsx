import Markdown from "react-markdown";

export function MarkdownContent({ children }: { children: string }) {
  return (
    <div className="markdown-content">
      <Markdown>{children}</Markdown>
    </div>
  );
}
