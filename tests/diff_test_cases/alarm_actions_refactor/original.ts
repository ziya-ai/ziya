import {
  Alarm,
  ComparisonOperator,
  Dashboard,
  Metric,
  TreatMissingData,
} from 'aws-cdk-lib/aws-cloudwatch';
import { CloudwatchDashboardsWiki } from '@amzn/cloudwatch-dashboards-wiki-cdk-construct';
import { App, Duration } from 'aws-cdk-lib';
import {
  simTicketAlarmActionSev3,
  simTicketAlarmActionSev4,
  simTicketAlarmActionSev5,
} from '../constants/monitoringConstants';
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

  private addAlarmActions(stage: StageName, alarm: Alarm) {
    if (stage === StageName.PROD) {
      alarm.addAlarmAction(simTicketAlarmActionSev3); //TODO: Update to Sev-2 later
    } else if (stage === StageName.GAMMA) {
      alarm.addAlarmAction(simTicketAlarmActionSev4);
    } else {
      alarm.addAlarmAction(simTicketAlarmActionSev5);
    }
  }
}
