import React from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';
import './CodeBlock.css';

interface CodeBlockProps {
  children: string;
  className?: string;
}

const CodeBlock: React.FC<CodeBlockProps> = ({ children, className }) => {
  const match = /language-(\w+)/.exec(className || '');
  const language = match ? match[1] : '';

  // Map common IaC languages to supported syntax highlighter languages
  const getLanguage = (lang: string): string => {
    switch (lang.toLowerCase()) {
      case 'cloudformation':
      case 'cfn':
        return 'yaml';
      case 'terraform':
      case 'tf':
      case 'hcl':
        return 'hcl';
      case 'json':
        return 'json';
      case 'yaml':
      case 'yml':
        return 'yaml';
      default:
        return lang || 'text';
    }
  };

  if (language) {
    return (
      <div className="code-block-container">
        <SyntaxHighlighter
          style={vscDarkPlus}
          language={getLanguage(language)}
          PreTag="div"
          customStyle={{
            margin: 0,
            borderRadius: '6px',
            fontSize: '14px',
            lineHeight: '1.4',
          }}
        >
          {String(children).replace(/\n$/, '')}
        </SyntaxHighlighter>
      </div>
    );
  }

  return (
    <code className={`inline-code ${className || ''}`}>
      {children}
    </code>
  );
};

export default CodeBlock;