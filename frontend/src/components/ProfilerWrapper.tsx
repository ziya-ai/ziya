import React, { Profiler, ProfilerOnRenderCallback } from 'react';

interface ProfilerWrapperProps {
  id: string;
  children: React.ReactNode;
  enabled?: boolean;
}

const onRenderCallback: ProfilerOnRenderCallback = (id, phase, actualDuration, baseDuration, startTime, commitTime) => {
  // Only log if the render took longer than 5ms
  if (actualDuration > 5) {
    console.log(`🔍 Profiler [${id}]:`, {
      phase,
      actualDuration: `${actualDuration.toFixed(2)}ms`,
      baseDuration: `${baseDuration.toFixed(2)}ms`,
      startTime: `${startTime.toFixed(2)}ms`,
      commitTime: `${commitTime.toFixed(2)}ms`
    });
  }
};

export const ProfilerWrapper: React.FC<ProfilerWrapperProps> = ({ 
  id, 
  children, 
  enabled = process.env.NODE_ENV === 'development' 
}) => {
  if (!enabled) {
    return <span>{children}</span>;
  }

  return <Profiler id={id} onRender={onRenderCallback}>{children}</Profiler>;
};
