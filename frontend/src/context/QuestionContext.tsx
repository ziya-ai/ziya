import React, { createContext, useContext, useState } from 'react';

// PERFORMANCE FIX: Split into two contexts to prevent unnecessary re-renders
// Components that only need setQuestion won't re-render when question changes

interface QuestionContextType {
  question: string;
}

interface QuestionSetterContextType {
  setQuestion: (question: string) => void;
}

const QuestionContext = createContext<QuestionContextType | undefined>(undefined);
const QuestionSetterContext = createContext<QuestionSetterContextType | undefined>(undefined);

export const QuestionProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [question, setQuestion] = useState('');

  // PERFORMANCE: Memoize setter to prevent re-creating on every render
  const setterValue = React.useMemo(() => ({ setQuestion }), []);
  const questionValue = React.useMemo(() => ({ question }), [question]);

  return (
    <QuestionSetterContext.Provider value={setterValue}>
      <QuestionContext.Provider value={questionValue}>
        {children}
      </QuestionContext.Provider>
    </QuestionSetterContext.Provider>
  );
};

// Hook for components that need to READ question (will re-render on change)
export const useQuestion = () => {
  const context = useContext(QuestionContext);
  if (!context) {
    throw new Error('useQuestion must be used within a QuestionProvider');
  }
  return context.question;
};

// Hook for components that only need to SET question (won't re-render on change)
export const useSetQuestion = () => {
  const context = useContext(QuestionSetterContext);
  if (!context) {
    throw new Error('useSetQuestion must be used within a QuestionProvider');
  }
  return context.setQuestion;
};

export const useQuestionContext = () => {
  const context = useContext(QuestionContext);
  if (!context) {
    throw new Error('useQuestionContext must be used within a QuestionProvider');
  }
  return context;
};
