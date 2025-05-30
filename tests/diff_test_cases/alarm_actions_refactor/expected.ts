import {
  Alarm,
  ComparisonOperator,
  Dashboard,
  Metric,
  TreatMissingData,
} from 'aws-cdk-lib/aws-cloudwatch';
import { CloudwatchDashboardsWiki } from '@amzn/cloudwatch-dashboards-wiki-cdk-construct';
import { App, Duration } from 'aws-cdk-lib';
import { SIMTicketAlarmAction } from '@amzn/sim-ticket-cdk-constructs';
import { Queue } from 'aws-cdk-lib/aws-sqs';
import { IFunction } from 'aws-cdk-lib/aws-lambda';
import { StageRegionConfig } from '../config/stages';
import { DeploymentStack } from './DeploymentStack';
import { StageName } from '../types';

export class CloudWatchMonitoringCDKStack extends DeploymentStack {
  // Other class methods and properties...

  private createMetric = () => {
    // Implementation details...
  };

  private addAlarmActions(
    stage: StageName, 
    alarm: Alarm, 
    prodAlarmAction?: SIMTicketAlarmAction,
    gammaAlarmAction?: SIMTicketAlarmAction,
    betaAlarmAction?: SIMTicketAlarmAction
  ) {
    if (stage === StageName.PROD) {
      alarm.addAlarmAction(prodAlarmAction || simTicketAlarmActionSev3);
    } else if (stage === StageName.GAMMA) {
      alarm.addAlarmAction(gammaAlarmAction || simTicketAlarmActionSev4);
    } else {
      alarm.addAlarmAction(betaAlarmAction || simTicketAlarmActionSev5);
    }
  }
}
