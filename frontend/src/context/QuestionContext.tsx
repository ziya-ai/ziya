import React, { createContext, useContext, useState } from 'react';

interface QuestionContextType {
  question: string;
  setQuestion: (question: string) => void;
}

const QuestionContext = createContext<QuestionContextType | undefined>(undefined);

export const QuestionProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [question, setQuestion] = useState('');

  // Simple object creation - no memoization needed for this simple case
  const value = {
    question,
    setQuestion
  };

  return (
    <QuestionContext.Provider value={value}>
      {children}
    </QuestionContext.Provider>
  );
};

export const useQuestionContext = () => {
  const context = useContext(QuestionContext);
  if (!context) {
    throw new Error('useQuestionContext must be used within a QuestionProvider');
  }
  return context;
};
