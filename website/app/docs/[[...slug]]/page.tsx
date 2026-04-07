import type { ComponentProps } from "react";
import { source } from "@/lib/source";
import { notFound } from "next/navigation";
import { findNeighbour } from "fumadocs-core/page-tree";
import { DocsToc } from "@/components/DocsToc";
import { CopyPageButton } from "@/components/CopyPageButton";
import { CopyButton } from "@/components/CopyButton";

function CodePre({ className, children, ...props }: ComponentProps<'pre'>) {
  return (
    <pre {...props} className={`d-code-pre${className ? ` ${className}` : ''}`}>
      {children}
      <CopyButton />
    </pre>
  );
}

const mdxComponents = { pre: CodePre };

export default async function Page(props: {
  params: Promise<{ slug?: string[] }>;
}) {
  const params = await props.params;
  const page = source.getPage(params.slug);
  if (!page) notFound();

  const MDX = page.data.body;
  const toc = page.data.toc;
  const markdown = await page.data.getText('raw');
  const { previous, next } = findNeighbour(source.pageTree, page.url);

  return (
    <>
      <article className="d-article">
        <header className="d-article-header">
          <div className="d-article-header-row">
            <h1 className="d-article-title">{page.data.title}</h1>
            <CopyPageButton markdown={markdown} />
          </div>
          {page.data.description && (
            <p className="d-article-desc">{page.data.description}</p>
          )}
        </header>
        <div className="d-prose">
          <MDX components={mdxComponents} />
        </div>
        {(previous || next) && (
          <nav className="d-pager">
            {previous ? (
              <a href={previous.url} className="d-pager-link d-pager-prev">
                <span className="d-pager-dir">&larr; Previous</span>
                <span className="d-pager-title">{previous.name}</span>
              </a>
            ) : (
              <span />
            )}
            {next ? (
              <a href={next.url} className="d-pager-link d-pager-next">
                <span className="d-pager-dir">Next &rarr;</span>
                <span className="d-pager-title">{next.name}</span>
              </a>
            ) : (
              <span />
            )}
          </nav>
        )}
      </article>
      <DocsToc items={toc} />
    </>
  );
}

export function generateStaticParams() {
  return source.generateParams();
}

export async function generateMetadata(props: {
  params: Promise<{ slug?: string[] }>;
}) {
  const params = await props.params;
  const page = source.getPage(params.slug);
  if (!page) notFound();

  return {
    title: `${page.data.title} - kensa docs`,
    description: page.data.description,
  };
}
