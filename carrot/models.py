from django.db import models
from django.core.urlresolvers import reverse
from django.utils.html import format_html, mark_safe
from django.forms.utils import flatatt
import json
import os
import sys
from django.core.validators import MinValueValidator


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR + '/carrot')


class MessageLog(models.Model):
    """
    MessageLogs store information about a carrot task

    Lifecycle:
        #. A :class:`carrot.objects.Message` object is created and published.
        #. The act of publishing the message creates a MessageLog object with the status 'PUBLISHED'. The task now sits
           in the RabbitMQ queue until it has been consumed
        #. When a consumer digests the message, the status is updated to 'COMPLETED' if the task completes successfully
           or 'FAILED' if it encounters an exception. The output, traceback, exception message and logs are written
           back to the MessageLog object
        #. If a task has failed, it can be requeued. Requeueing a task will create a new :class:`carrot.objects.Message`
           object with the same parameters. In this case, the originally MessageLog object will be deleted
        #. If the task has been completed successfully, it will be deleted three days after completion, provided that
           the :function:`carrot.helper_tasks.cleanup` has not been disabled

    """
    STATUS_CHOICES = (
        ('UNPUBLISHED', 'Not yet published'),
        ('PUBLISHED', 'Published'),
        ('FAILED', 'Failed'),
        ('COMPLETED', 'Completed'),
    ) #:

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='UNPUBLISHED')
    exchange = models.CharField(max_length=200, blank=True, null=True) #: the exchange
    queue = models.CharField(max_length=200, blank=True, null=True)
    routing_key = models.CharField(max_length=200, blank=True, null=True)
    uuid = models.CharField(max_length=200)
    priority = models.PositiveIntegerField(default=0)

    task = models.CharField(max_length=200)#: the import path for the task to be executed
    task_args = models.TextField(null=True, blank=True, verbose_name='Task positional arguments')
    content = models.TextField(null=True, blank=True, verbose_name='Task keyword arguments')

    exception = models.TextField(null=True, blank=True)
    traceback = models.TextField(null=True, blank=True)
    output = models.TextField(null=True, blank=True)

    publish_time = models.DateTimeField(null=True, blank=True)
    failure_time = models.DateTimeField(null=True, blank=True)
    completion_time = models.DateTimeField(null=True, blank=True)

    log = models.TextField(blank=True, null=True)

    @property
    def virtual_host(self):
        from carrot.utilities import get_host_from_name
        return get_host_from_name(self.queue)

    @property
    def parsed_content(self):
        """
        Used in :class:`carrot.views.MessageView` to display the keyword arguments as a table
        """
        return mark_safe(json.dumps(self.content, indent=4, sort_keys=True))

    def __str__(self):
        return self.task

    def get_url(self):
        """
        Creates a url that points to :class:`carrot.views.MessageView`
        """
        return reverse('task-info', args=[self.pk])

    @property
    def href(self):
        """
        Renders self.get_url() as a HTML href
        """
        return format_html('<a {}>{}</a>', flatatt({'href': self.get_url()}), self.task)

    def display_time(self, time):
        """
        Helper function to format time for display
        """
        if time:
            return time.strftime('%Y-%m-%d %I:%M %p')

    @property
    def display_publish_time(self):
        return self.display_time(self.publish_time)

    @property
    def display_completion_time(self):
        return self.display_time(self.completion_time)

    @property
    def display_failure_time(self):
        return self.display_time(self.failure_time)

    @property
    def retry_url(self):
        """
        Returns the URL required for requeueing the task
        """
        return reverse('requeue-task', args=[self.pk])

    @property
    def delete_url(self):
        """
        Returns the URL required for requeueing the task
        """
        return reverse('delete-task', args=[self.pk])

    # noinspection PyTypeChecker
    def requeue(self):
        """
        Sends a failed MessageLog back to the queue. The original MessageLog is deleted
        """
        from carrot.objects import Message
        from carrot.utilities import get_host_from_name
        host = get_host_from_name(self.queue)

        new_message = Message(
            virtual_host=host,
            queue=self.queue,
            routing_key=self.routing_key,
            exchange='' if self.exchange == 'default' else self.exchange,
            task=self.task,
            priority=self.priority,
            task_args=self.task_args,
            task_kwargs=self.content,
        )
        new_message.publish()
        self.delete()

    class Meta:
        ordering = '-priority', 'pk',


class ScheduledTask(models.Model):
    """
    A model for scheduling tasks to run at a certain interval
    """

    INTERVAL_CHOICES = (
        ('seconds', 'seconds'),
        ('minutes', 'minutes'),
        ('hours', 'hours'),
        ('days', 'days'),
    )

    interval_type = models.CharField(max_length=200, choices=INTERVAL_CHOICES, default='seconds')
    interval_count = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    exchange = models.CharField(max_length=200, blank=True, null=True)
    routing_key = models.CharField(max_length=200, blank=True, null=True)
    queue = models.CharField(max_length=200, blank=True, null=True)
    task = models.CharField(max_length=200)
    task_args = models.TextField(null=True, blank=True, verbose_name='Positional arguments')
    content = models.TextField(null=True, blank=True, verbose_name='Keyword arguments')

    active = models.BooleanField(default=True)

    def get_absolute_url(self):
        return reverse('edit-scheduled-task', args=[self.pk])

    @property
    def interval_display(self):
        return 'Every %i %s' % (self.interval_count, self.interval_type if self.interval_count > 1 else
            self.interval_type[:-1])

    @property
    def multiplier(self):
        if self.interval_type == 'minutes':
            return 60

        if self.interval_type == 'hours':
            return 60 * 60

        if self.interval_type == 'days':
            return 86400

        return 1

    @property
    def positional_arguments(self):
        if self.task_args:
            return tuple([a.strip() for a in self.task_args.split(',') if a])
        else:
            return ()

    def publish(self, priority=0):
        from carrot.utilities import publish_message
        return publish_message(self.task, priority, self.queue, self.exchange or '',
                               self.routing_key or self.queue, *self.positional_arguments,
                               json.loads(self.content or '{}'))

    def __str__(self):
        return self.task

    @property
    def href(self):
        return format_html('<a {}>{}</a>', flatatt({'href': reverse('edit-scheduled-task', args=[self.pk])}), self.task)

    @property
    def delete_href(self):
        return format_html('<a {}>{}</a>', flatatt({'href': reverse('delete-scheduled-task', args=[self.pk])}),
                           self.task)

